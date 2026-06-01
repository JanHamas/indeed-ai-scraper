STEP 0 — What you need (2 minutes)

You already have most of this:
✅ Laptop
✅ Internet
✅ Basic Python (you have this)
❌ Apify account (we’ll create now)
sudo apt update
sudo apt install python3.12-venv


STEP 1 — Create an Apify account
Open browser
Go to Apify
Sign up (Google / email both fine)
Open Apify Console
👉 Stop here when you see Dashboard / Actors

STEP 2 — Install Apify CLI (one time only)
Using the NodeSource PPA (Newer Versions with apt) 
This method is a good compromise, allowing you to use newer or specific Node.js versions via the apt package manager. 
Install curl if you don't have it already:
sudo apt install curl -y
Add the NodeSource repository for your desired version (e.g., for Node.js 22.x, or check NodeSource documentation for other versions):
bash
curl -sL https://deb.nodesource.com | sudo -E bash -
Install Node.js:
sudo apt install nodejs -y
This command automatically installs the correct nodejs package and the corresponding npm.
Verify the installation:
node -v
npm -v
 

2.2 Install Apify CLI
npm install -g apify-cli

STEP 3 — Login to Apify from terminal
apify login
Browser opens
Allow access
Done ✅

Verify:
apify --version

STEP 4 — Create your FIRST Actor (IMPORTANT)
In terminal, run:
apify create indeed-ai-jobs-scraper
When asked:
Language → Python
Template → playwright-python
👉 This is the best starting template
 
 STEP 5 — Understand folder (very important)
 
Open the folder:
my-first-scraper/
├── src/
│   └── main.py   ← YOU edit this
├── apify.json    ← actor settings
├── requirements.txt
└── Dockerfile


👉 Only focus on src/main.py for now

STEP 6 — Write the SIMPLEST scraper

Open src/main.py
Replace everything with this 👇
from apify import Actor
from playwright.async_api import async_playwright

async def main():
    async with Actor:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.goto("https://example.com")
            title = await page.title()

            await Actor.push_data({
                "url": "https://example.com",
                "title": title
            })

            await browser.close()
This does ONLY:

Open website

Get title

Save result

STEP 7 — Run Actor locally (TEST)

In project folder:
apify run
You should see:
No errors
Dataset created
👉 If this works → you’re 70% done already.

STEP 8 — Push Actor to Apify Cloud 🚀

Now upload it:
apify push
Go to Apify Console → Actors → my-first-scraper
Click Run
🎉 Your Actor is LIVE on cloud.

STEP 9 — View Output (Very important)

After run:
Open Dataset
You’ll see table like:

url	title
example.com	Example Domain

This is how Apify stores scraped data.
