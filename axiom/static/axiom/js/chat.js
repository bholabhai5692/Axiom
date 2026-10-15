const shell = document.querySelector("#app-shell");
const composer = document.querySelector("#composer");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const messageList = document.querySelector("#message-list");
const messageScroll = document.querySelector("#message-scroll");
const errorNotice = document.querySelector("#error-notice");
const scrapeSteps = [
  "Start browser",
  "Open chat URL",
  "Load chat page",
  "Check target access",
  "Find chat input",
  "Prepare prompt",
  "Enter prompt",
  "Submit message",
  "Wait for reply",
  "Read response",
  "Complete",
];

function scrollToLatest() {
  messageScroll.scrollTop = messageScroll.scrollHeight;
}

function addMessage(role, content, pending = false) {
  const article = document.createElement("article");
  article.className = `message message-${role}${pending ? " message-pending" : ""}`;
  const avatar = document.createElement("div");
  avatar.className = `message-avatar${role === "assistant" ? " assistant-avatar" : ""}`;
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = role === "assistant" ? "✳" : "G";
  const body = document.createElement("div");
  body.className = "message-body";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = role === "assistant" ? "AXIOM" : "YOU";
  const contentNode = document.createElement("div");
  contentNode.className = "message-content";
  contentNode.textContent = content;
  body.append(meta, contentNode);
  article.append(avatar, body);
  messageList.append(article);
  scrollToLatest();
  return article;
}

function addProgressTracker(message) {
  const tracker = document.createElement("ol");
  tracker.className = "scrape-progress";
  tracker.setAttribute("aria-label", "Scraper progress");
  tracker.setAttribute("aria-live", "polite");
  for (const [index, label] of scrapeSteps.entries()) {
    const item = document.createElement("li");
    item.className = "scrape-step";
    item.dataset.step = index;
    const marker = document.createElement("span");
    marker.className = "scrape-step-marker";
    marker.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = label;
    item.append(marker, text);
    tracker.append(item);
  }
  message.querySelector(".message-body").append(tracker);
  scrollToLatest();
  return tracker;
}

function updateProgressTracker(tracker, progress) {
  const currentStep = Number(progress.step);
  for (const item of tracker.querySelectorAll(".scrape-step")) {
    const step = Number(item.dataset.step);
    item.classList.toggle("is-complete", progress.status === "complete" || step < currentStep);
    item.classList.toggle("is-active", progress.status === "active" && step === currentStep);
    item.classList.toggle("is-error", progress.status === "error" && step === currentStep);
  }
  tracker.dataset.status = progress.status || "active";
  tracker.dataset.message = progress.message || "";
}

function startProgressPolling(tracker, onProgress) {
  let stopped = false;
  let timeoutId;

  async function poll() {
    if (stopped) return;
    try {
      const response = await fetch(shell.dataset.progressUrl, {
        headers: { Accept: "application/json" },
      });
      if (stopped) return;
      if (response.ok) {
        const progress = await response.json();
        if (stopped) return;
        updateProgressTracker(tracker, progress);
        onProgress(progress);
      }
    } catch {
      // The message request will surface the actionable failure if polling is unavailable.
    }
    if (!stopped) timeoutId = setTimeout(poll, 300);
  }

  poll();
  return () => {
    stopped = true;
    clearTimeout(timeoutId);
  };
}

async function sendMessage(text) {
  const content = text.trim();
  if (!content || sendButton.disabled) return;
  errorNotice.hidden = true;
  document.querySelector("#welcome")?.remove();
  addMessage("user", content);
  const pending = addMessage("assistant", "Thinking…", true);
  const tracker = addProgressTracker(pending);
  let latestProgress = { step: 0, status: "active", message: "Starting headless browser" };
  const stopProgressPolling = startProgressPolling(tracker, (progress) => {
    latestProgress = progress;
  });
  input.value = "";
  input.style.height = "auto";
  sendButton.disabled = true;

  try {
    const response = await fetch(shell.dataset.sendUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": composer.querySelector("[name=csrfmiddlewaretoken]").value,
      },
      body: JSON.stringify({ content }),
    });
    const responseText = await response.text();
    let result;
    try {
      result = responseText ? JSON.parse(responseText) : null;
    } catch {
      throw new Error(
        `Server returned an invalid response (HTTP ${response.status}). Check the Django server logs.`,
      );
    }
    if (!result || typeof result !== "object") {
      throw new Error(
        `Server returned an empty response (HTTP ${response.status}). Check the Django server logs.`,
      );
    }
    if (!response.ok) {
      const stage = result.stage ? `Step: ${result.stage}. ` : "";
      const hint = result.hint ? ` ${result.hint}` : "";
      if (result.step !== undefined) {
        latestProgress = {
          step: result.step,
          stage: result.stage,
          status: "error",
          message: result.error,
        };
        updateProgressTracker(tracker, latestProgress);
      }
      throw new Error(`${stage}${result.error || "Your message could not be sent."}${hint}`);
    }
    if (!result.assistant || typeof result.assistant.content !== "string") {
      throw new Error("Server response is missing the assistant message. Check the Django logs.");
    }
    latestProgress = { step: 10, stage: "complete", status: "complete", message: "Reply ready" };
    updateProgressTracker(tracker, latestProgress);
    pending.classList.remove("message-pending");
    pending.querySelector(".message-content").textContent = result.assistant.content;
    if (result.title) document.querySelector(".breadcrumb strong").textContent = result.title;
    const activeConversation = document.querySelector(".conversation-link.is-active span:last-child");
    if (activeConversation && result.title) activeConversation.textContent = result.title;
  } catch (error) {
    pending.classList.remove("message-pending");
    pending.querySelector(".message-content").textContent = "Scrape stopped at the step shown below.";
    if (latestProgress.status !== "error") {
      latestProgress = { ...latestProgress, status: "error", message: error.message };
      updateProgressTracker(tracker, latestProgress);
    }
    errorNotice.textContent = error.message || "Something went wrong. Please try again.";
    errorNotice.hidden = false;
    scrollToLatest();
  } finally {
    stopProgressPolling();
    sendButton.disabled = false;
    input.focus();
  }
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(input.value);
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.prompt;
    composer.requestSubmit();
  });
});

const sidebar = document.querySelector("#sidebar");
const scrim = document.querySelector("#mobile-scrim");
document.querySelector("#mobile-menu").addEventListener("click", () => {
  sidebar.classList.add("is-open");
  scrim.classList.add("is-open");
});
scrim.addEventListener("click", () => {
  sidebar.classList.remove("is-open");
  scrim.classList.remove("is-open");
});

scrollToLatest();