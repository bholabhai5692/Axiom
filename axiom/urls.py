from django.urls import path

from . import views

app_name = "axiom"

urlpatterns = [
    path("", views.index, name="home"),
    path("conversations/new/", views.new_conversation, name="new_conversation"),
    path("conversations/<uuid:conversation_id>/", views.conversation_view, name="conversation"),
    path(
        "api/conversations/<uuid:conversation_id>/messages/",
        views.send_message,
        name="send_message",
    ),
    path(
        "api/conversations/<uuid:conversation_id>/progress/",
        views.scrape_progress,
        name="scrape_progress",
    ),
]