from django.conf import settings
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


POLL_INTERVAL_SECONDS = 0.25
STABLE_POLLS_REQUIRED = 2
MAX_CONTEXT_MESSAGES = 12
MAX_CONTEXT_MESSAGE_LENGTH = 3000

MASTER_PROMPT = """You are a helpful assistant. Reply in the language used by the user. Be accurate, clear, and concise. Use readable Markdown by default. When the user requests JSON or the answer requires JSON, return one complete, syntactically valid JSON document; do not truncate it, omit closing brackets, or put commentary outside the JSON."""


class ChatScraperError(Exception):
    def __init__(self, message, *, stage, hint):
        super().__init__(message)
        self.stage = stage
        self.hint = hint


def _build_prompt(history, user_message):
    context = [MASTER_PROMPT]
    if history:
        context.append("Earlier conversation for context:")
    for message in history[-MAX_CONTEXT_MESSAGES:]:
        sender = "User" if message.role == "user" else "NoTrack"
        content = message.content[-MAX_CONTEXT_MESSAGE_LENGTH:]
        context.append(f"{sender}: {content}")
    context.append(
        "Use the conversation above as context. Reply to this new user message:\n"
        f"{user_message}"
    )
    return "\n\n".join(context)


def _has_unclosed_json_bracket(text):
    stack = []
    in_string = False
    escaped = False
    closing_for = {"{": "}", "[": "]"}

    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in closing_for:
            stack.append(closing_for[character])
        elif character in "}]":
            if not stack or stack.pop() != character:
                return False
    return bool(stack)


def _read_latest_reply(driver, previous_bubble_count):
    bubbles = driver.find_elements(By.CSS_SELECTOR, ".bubble")
    for bubble in reversed(bubbles[previous_bubble_count:]):
        try:
            sender = bubble.find_element(By.CSS_SELECTOR, ".meta strong").text
            if "notrack" not in sender.casefold():
                continue
            content = bubble.find_element(
                By.CSS_SELECTOR, ".message-body .markdown-body"
            ).text
        except (NoSuchElementException, StaleElementReferenceException):
            continue
        if content.strip():
            return content.strip()
    return None


def _wait_for_reply(driver, previous_bubble_count, wait):
    state = {"last_content": None, "stable_polls": 0}

    def response_is_complete(browser):
        content = _read_latest_reply(browser, previous_bubble_count)
        if not content or _has_unclosed_json_bracket(content):
            state["last_content"] = None
            state["stable_polls"] = 0
            return False
        if content == state["last_content"]:
            state["stable_polls"] += 1
        else:
            state["last_content"] = content
            state["stable_polls"] = 0
        if state["stable_polls"] >= STABLE_POLLS_REQUIRED:
            return content
        return False

    return wait.until(response_is_complete)


def _create_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    if settings.AXIOM_CHROME_BINARY:
        options.binary_location = settings.AXIOM_CHROME_BINARY

    service = Service(executable_path=settings.AXIOM_CHROMEDRIVER_PATH or None)
    return webdriver.Chrome(options=options, service=service)


def _report_progress(progress_callback, step, stage, message):
    if progress_callback:
        progress_callback({"step": step, "stage": stage, "message": message})


def generate_reply(history, user_message, progress_callback=None):
    driver = None
    stage = "browser startup"
    try:
        _report_progress(progress_callback, 0, "browser_start", "Starting headless browser")
        driver = _create_driver()
        stage = "page navigation"
        _report_progress(progress_callback, 1, "open_url", "Opening NoTrack chat URL")
        driver.set_page_load_timeout(settings.AXIOM_SELENIUM_TIMEOUT)
        driver.get(settings.AXIOM_CHAT_URL)

        _report_progress(progress_callback, 2, "load_page", "Chat page loaded")
        stage = "target access check"
        _report_progress(progress_callback, 3, "check_access", "Checking target access")
        page_text = driver.find_element(By.TAG_NAME, "body").text
        if "automated access is not allowed" in page_text.casefold():
            raise ChatScraperError(
                "NoTrack refused automated access to this page.",
                stage="target access",
                hint="Use a target or integration that permits automation; do not bypass this block.",
            )

        stage = "input selector #field"
        _report_progress(progress_callback, 4, "find_input", "Finding chat input")
        wait = WebDriverWait(
            driver,
            settings.AXIOM_SELENIUM_TIMEOUT,
            poll_frequency=POLL_INTERVAL_SECONDS,
        )
        chat_input = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "#field"))
        )
        _report_progress(progress_callback, 5, "prepare_prompt", "Preparing conversation prompt")
        previous_bubble_count = len(
            driver.find_elements(By.CSS_SELECTOR, ".bubble")
        )
        prompt = _build_prompt(history, user_message)
        chat_input.clear()
        _report_progress(progress_callback, 6, "enter_prompt", "Entering prompt in chat")
        chat_input.send_keys(prompt)
        stage = "submit selector #goBtn"
        _report_progress(progress_callback, 7, "submit", "Submitting message")
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#goBtn"))).click()
        stage = "response polling"
        _report_progress(progress_callback, 8, "wait_reply", "Waiting for reply to finish")
        reply = _wait_for_reply(driver, previous_bubble_count, wait)
        _report_progress(progress_callback, 9, "read_response", "Reading completed response")
        return reply
    except ChatScraperError:
        raise
    except TimeoutException as exc:
        raise ChatScraperError(
            f"Timed out during {stage}.",
            stage=stage,
            hint="Check the page response, selector, and configured timeout.",
        ) from exc
    except WebDriverException as exc:
        if stage == "browser startup":
            message = (
                "Could not start headless Chrome. Minimal Ubuntu containers need Chrome "
                "runtime libraries for GTK/AT-SPI, CUPS, GBM, XKB, and audio."
            )
            hint = "Install the documented libraries and verify the browser/driver paths."
        else:
            message = f"Chrome/WebDriver failed during {stage}."
            hint = "Check the Selenium/ChromeDriver logs and the target page response."
        raise ChatScraperError(
            message,
            stage=stage,
            hint=hint,
        ) from exc
    finally:
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException:
                pass