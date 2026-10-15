# Axiom

A Django chat workspace that uses Selenium to interact with the NoTrack chat page. It does not call an AI API.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000. Install Chrome or Chromium and its runtime libraries in the environment running Django. Minimal Linux containers may need GTK, GBM, AT-SPI, XKB, and audio libraries. Selenium Manager can locate or download a compatible ChromeDriver; if browser discovery fails, set the paths explicitly:

```bash
export AXIOM_CHAT_URL="https://notrack.ai/chat"
export AXIOM_CHROME_BINARY="/usr/bin/chromium"
export AXIOM_CHROMEDRIVER_PATH="/usr/bin/chromedriver"
export AXIOM_SELENIUM_TIMEOUT="120"
```

On Ubuntu 24.04 containers, install Chrome's common runtime libraries with:

```bash
sudo apt-get update
sudo apt-get install -y libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libxkbcommon0 libasound2t64 libgbm1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libatspi2.0-0t64
```

The scraper uses `#field` for input, `#goBtn` to submit, `.bubble` for messages, `.meta strong` to identify NoTrack replies, and `.message-body .markdown-body` for response text. It polls every 250 ms until the response is stable and any open JSON brackets are closed. Selenium starts a fresh headless browser per message and includes recent saved messages as conversation context. Each browser session sees only its own saved conversations; there is no user account system yet.

## Verify

```bash
python manage.py test axiom.test_chat
python manage.py check
```