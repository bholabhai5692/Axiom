import json
import logging

from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .models import Conversation, Message
from .services import ChatScraperError, generate_reply


MAX_MESSAGE_LENGTH = 12000
MAX_HISTORY_MESSAGES = 30
logger = logging.getLogger(__name__)
PROGRESS_CACHE_TTL = 300


def _progress_key(conversation_id):
    return f"axiom:scrape-progress:{conversation_id}"


def _session_key(request):
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def _conversation_for_session(request, conversation_id):
    return get_object_or_404(
        Conversation, id=conversation_id, session_key=_session_key(request)
    )


def index(request):
    session_key = _session_key(request)
    conversations = Conversation.objects.filter(session_key=session_key)
    if not conversations.exists():
        conversation = Conversation.objects.create(session_key=session_key)
    else:
        conversation = conversations.first()
    return _render_chat(request, conversation)


def conversation_view(request, conversation_id):
    conversation = _conversation_for_session(request, conversation_id)
    return _render_chat(request, conversation)


def _render_chat(request, conversation):
    conversations = Conversation.objects.filter(session_key=_session_key(request))
    return render(
        request,
        "axiom/chat.html",
        {
            "conversation": conversation,
            "conversations": conversations,
            "messages": conversation.messages.all(),
            "send_url": reverse("axiom:send_message", args=[conversation.id]),
            "progress_url": reverse("axiom:scrape_progress", args=[conversation.id]),
        },
    )


@require_POST
def new_conversation(request):
    conversation = Conversation.objects.create(session_key=_session_key(request))
    return redirect("axiom:conversation", conversation_id=conversation.id)


@require_GET
def scrape_progress(request, conversation_id):
    _conversation_for_session(request, conversation_id)
    progress = cache.get(_progress_key(conversation_id))
    if progress is None:
        progress = {"step": -1, "stage": "idle", "status": "idle", "message": ""}
    return JsonResponse(progress)


@require_POST
def send_message(request, conversation_id):
    conversation = _conversation_for_session(request, conversation_id)
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Send a valid JSON message."}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "Send a valid JSON message."}, status=400)
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return JsonResponse({"error": "Write a message before sending."}, status=400)
    content = content.strip()
    if len(content) > MAX_MESSAGE_LENGTH:
        return JsonResponse(
            {"error": f"Messages must be {MAX_MESSAGE_LENGTH:,} characters or fewer."},
            status=400,
        )

    history = list(
        conversation.messages.order_by("-created_at", "-id")[:MAX_HISTORY_MESSAGES]
    )
    history.reverse()
    progress_key = _progress_key(conversation.id)

    def report_progress(progress):
        cache.set(
            progress_key,
            {**progress, "status": "active"},
            timeout=PROGRESS_CACHE_TTL,
        )

    report_progress(
        {"step": 0, "stage": "browser_start", "message": "Starting headless browser"}
    )
    try:
        reply = generate_reply(history, content, progress_callback=report_progress)
    except ChatScraperError as exc:
        progress = cache.get(progress_key, {"step": 0})
        cache.set(
            progress_key,
            {
                **progress,
                "stage": exc.stage,
                "status": "error",
                "message": str(exc),
                "hint": exc.hint,
            },
            timeout=PROGRESS_CACHE_TTL,
        )
        return JsonResponse(
            {
                "error": str(exc),
                "stage": exc.stage,
                "hint": exc.hint,
                "step": progress.get("step", 0),
            },
            status=502,
        )
    except Exception:
        logger.exception("Unexpected error while scraping the chat response.")
        cache.set(
            progress_key,
            {
                "step": cache.get(progress_key, {}).get("step", 0),
                "stage": "server",
                "status": "error",
                "message": "Unexpected server error while scraping.",
                "hint": "Check the Django server logs for details, then retry.",
            },
            timeout=PROGRESS_CACHE_TTL,
        )
        return JsonResponse(
            {
                "error": "An unexpected server error occurred while scraping.",
                "stage": "server",
                "hint": "Check the Django server logs for details, then retry.",
                "step": cache.get(progress_key, {}).get("step", 0),
            },
            status=500,
        )

    cache.set(
        progress_key,
        {"step": 10, "stage": "complete", "status": "complete", "message": "Reply ready"},
        timeout=PROGRESS_CACHE_TTL,
    )
    if conversation.title == "New conversation":
        conversation.title = content[:77] + ("..." if len(content) > 77 else "")
    conversation.save(update_fields=["title", "updated_at"])
    user_message = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content=content
    )
    assistant_message = Message.objects.create(
        conversation=conversation, role=Message.Role.ASSISTANT, content=reply
    )
    return JsonResponse(
        {
            "user": {"role": user_message.role, "content": user_message.content},
            "assistant": {
                "role": assistant_message.role,
                "content": assistant_message.content,
            },
            "title": conversation.title,
        }
    )