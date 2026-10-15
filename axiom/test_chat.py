import json
from unittest.mock import ANY, MagicMock, call, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from selenium.common.exceptions import TimeoutException, WebDriverException

from axiom.models import Conversation, Message
from axiom.services import (
    ChatScraperError,
    MASTER_PROMPT,
    _build_prompt,
    _has_unclosed_json_bracket,
    generate_reply,
)


class ChatTests(TestCase):
    def test_home_creates_a_session_conversation(self):
        response = self.client.get(reverse("axiom:home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertContains(response, "Good questions")

    @override_settings(AXIOM_CHAT_URL="https://notrack.ai/chat")
    @patch("axiom.views.generate_reply", return_value="A scraped answer.")
    def test_scraped_reply_is_saved_to_the_conversation(self, mocked_scraper):
        self.client.get(reverse("axiom:home"))
        conversation = Conversation.objects.get()

        response = self.client.post(
            reverse("axiom:send_message", args=[conversation.id]),
            data=json.dumps({"content": "Hello"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["assistant"]["content"], "A scraped answer.")
        self.assertEqual(conversation.messages.count(), 2)
        self.assertEqual(conversation.messages.first().role, Message.Role.USER)
        mocked_scraper.assert_called_once_with(
            [], "Hello", progress_callback=ANY
        )
        progress_response = self.client.get(
            reverse("axiom:scrape_progress", args=[conversation.id])
        )
        self.assertEqual(progress_response.json()["status"], "complete")
        self.assertEqual(progress_response.json()["step"], 10)

    @patch("axiom.views.generate_reply")
    def test_progress_endpoint_exposes_mid_scrape_updates(self, mocked_scraper):
        self.client.get(reverse("axiom:home"))
        conversation = Conversation.objects.get()
        progress_url = reverse("axiom:scrape_progress", args=[conversation.id])
        observed_progress = {}

        def scrape_with_progress(_history, _content, progress_callback):
            progress_callback(
                {"step": 4, "stage": "enter_prompt", "message": "Entering prompt"}
            )
            observed_progress.update(self.client.get(progress_url).json())
            return "A scraped answer."

        mocked_scraper.side_effect = scrape_with_progress
        response = self.client.post(
            reverse("axiom:send_message", args=[conversation.id]),
            data=json.dumps({"content": "Hello"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed_progress["status"], "active")
        self.assertEqual(observed_progress["step"], 4)
        self.assertEqual(observed_progress["stage"], "enter_prompt")

    @patch(
        "axiom.views.generate_reply",
        side_effect=ChatScraperError(
            "NoTrack refused access.",
            stage="target access",
            hint="Use a permitted target.",
        ),
    )
    def test_scraper_errors_return_a_json_stage_and_hint(self, _mocked_scraper):
        self.client.get(reverse("axiom:home"))
        conversation = Conversation.objects.get()

        response = self.client.post(
            reverse("axiom:send_message", args=[conversation.id]),
            data=json.dumps({"content": "Hello"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["stage"], "target access")
        self.assertEqual(response.json()["hint"], "Use a permitted target.")
        progress = self.client.get(
            reverse("axiom:scrape_progress", args=[conversation.id])
        ).json()
        self.assertEqual(progress["status"], "error")
        self.assertEqual(progress["stage"], "target access")
        self.assertEqual(progress["step"], 0)

    def test_master_prompt_requires_complete_valid_json(self):
        self.assertIn("complete, syntactically valid JSON", MASTER_PROMPT)
        self.assertIn(MASTER_PROMPT, _build_prompt([], "Give me JSON"))

    def test_conversations_are_private_to_their_session(self):
        self.client.get(reverse("axiom:home"))
        conversation = Conversation.objects.get()
        other_client = self.client_class()

        response = other_client.get(reverse("axiom:conversation", args=[conversation.id]))

        self.assertEqual(response.status_code, 404)

    def test_empty_message_is_rejected(self):
        self.client.get(reverse("axiom:home"))
        conversation = Conversation.objects.get()

        response = self.client.post(
            reverse("axiom:send_message", args=[conversation.id]),
            data=json.dumps({"content": "   "}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(conversation.messages.count(), 0)

    def test_json_reply_waits_for_its_closing_bracket(self):
        self.assertTrue(_has_unclosed_json_bracket('{"answer": [1, 2'))
        self.assertFalse(_has_unclosed_json_bracket('{"answer": [1, 2]}'))
        self.assertFalse(_has_unclosed_json_bracket('{"answer": "["}'))

    def test_follow_up_context_is_included_in_scraper_prompt(self):
        history = [
            Message(role=Message.Role.USER, content="Earlier question"),
            Message(role=Message.Role.ASSISTANT, content="Earlier answer"),
        ]

        prompt = _build_prompt(history, "Follow-up")

        self.assertIn("User: Earlier question", prompt)
        self.assertIn("NoTrack: Earlier answer", prompt)
        self.assertTrue(prompt.endswith("Follow-up"))

    @override_settings(
        AXIOM_CHAT_URL="https://notrack.ai/chat",
        AXIOM_SELENIUM_TIMEOUT=10,
        AXIOM_CHROME_BINARY="",
        AXIOM_CHROMEDRIVER_PATH="",
    )
    @patch("axiom.services.WebDriverWait")
    @patch("axiom.services.webdriver.Chrome")
    def test_selenium_scrapes_nostrack_markdown(self, mocked_chrome, mocked_wait):
        driver = MagicMock()
        mocked_chrome.return_value = driver
        chat_input = MagicMock()
        chat_input.is_displayed.return_value = True
        chat_input.is_enabled.return_value = True
        send_button = MagicMock()
        send_button.is_displayed.return_value = True
        send_button.is_enabled.return_value = True
        page_body = MagicMock(text="NoTrack chat page")
        driver.find_element.side_effect = lambda _by, selector: {
            "#field": chat_input,
            "#goBtn": send_button,
            "body": page_body,
        }[selector]
        sender = MagicMock(text="NoTrack")
        answer = MagicMock(text='{"ok": true}')
        bot_bubble = MagicMock()
        bot_bubble.find_element.side_effect = [sender, answer, sender, answer, sender, answer]
        user_bubble = MagicMock()
        driver.find_elements.side_effect = [
            [],
            [user_bubble, bot_bubble],
            [user_bubble, bot_bubble],
            [user_bubble, bot_bubble],
        ]

        class ImmediateWait:
            def __init__(self, *_args, **_kwargs):
                pass

            def until(self, condition):
                for _ in range(5):
                    result = condition(driver)
                    if result:
                        return result
                raise TimeoutException()

        mocked_wait.side_effect = ImmediateWait

        progress = MagicMock()
        result = generate_reply([], "Hello", progress_callback=progress)

        self.assertEqual(result, '{"ok": true}')
        driver.get.assert_called_once_with("https://notrack.ai/chat")
        chat_input.send_keys.assert_called_once_with(_build_prompt([], "Hello"))
        send_button.click.assert_called_once()
        driver.quit.assert_called_once()
        self.assertEqual(
            [entry.args[0]["stage"] for entry in progress.call_args_list],
            [
                "browser_start",
                "open_url",
                "load_page",
                "check_access",
                "find_input",
                "prepare_prompt",
                "enter_prompt",
                "submit",
                "wait_reply",
                "read_response",
            ],
        )

    @override_settings(
        AXIOM_CHAT_URL="https://notrack.ai/chat",
        AXIOM_SELENIUM_TIMEOUT=10,
        AXIOM_CHROME_BINARY="",
        AXIOM_CHROMEDRIVER_PATH="",
    )
    @patch("axiom.services.webdriver.Chrome")
    def test_target_access_denial_fails_at_its_own_progress_step(self, mocked_chrome):
        driver = MagicMock()
        driver.find_element.return_value.text = (
            "403 Forbidden. Automated access is not allowed."
        )
        mocked_chrome.return_value = driver
        progress = MagicMock()

        with self.assertRaises(ChatScraperError) as error:
            generate_reply([], "Hello", progress_callback=progress)

        self.assertEqual(error.exception.stage, "target access")
        self.assertEqual(progress.call_args_list[-1].args[0]["stage"], "check_access")
        driver.quit.assert_called_once()

    @patch(
        "axiom.services.webdriver.Chrome",
        side_effect=WebDriverException("missing shared library"),
    )
    def test_browser_start_error_mentions_runtime_libraries(self, _mocked_chrome):
        with self.assertRaisesRegex(ChatScraperError, "GTK/AT-SPI") as error:
            generate_reply([], "Hello")
        self.assertEqual(error.exception.stage, "browser startup")