import asyncio, json, os
from twocaptcha import TwoCaptcha
from dotenv import load_dotenv
from asgiref.sync import async_to_sync


load_dotenv()


class CloudflareBypasser:
    def __init__(self, page, log):
        self.page = page
        self.api_key = os.getenv("2CAPTCHA_API_KEY")
        self.captured_params = None
        self.console_listener = None
        self.log = log
     
    async def detect_and_bypass(self):
        # is_visible() with timeout=0 is instant — no waiting
        if not await self.page.locator("text='Additional Verification Required'").is_visible(timeout=0):
            return False  # fast path — no challenge, return immediately
        
        # some time captcha auto solve
        await self.log.emit("info", "[+] Attempting Cloudflare Bypass")
        # await self.page.reload()
        # await self.page.wait_for_timeout(5000)
        # if not await self.page.locator("text='Additional Verification Required'").is_visible(timeout=0):
        #     return False  
        
        
        params = await self.get_captcha_params()
        if params:
            token = await self.solve_captcha_async(params)
            if token:
                await self.send_token(token)
                await asyncio.sleep(5)
                await self.log.emit("info", "[+] Cloudflare Successfully Bypassed")
                return True
        await self.log.emit("critical", "[-] Cloudflare Bypass Failed")
        return False
    
    async def get_captcha_params(self):
        intercept_script = """
        console.clear = () => console.log("Console was cleared");
        let resolved = false;
        const intervalID = setInterval(() => {
            if (window.turnstile && !resolved) {
                clearInterval(intervalID);
                resolved = true;
                window.turnstile.render = (a, b) => {
                    const params = {
                        sitekey: b.sitekey,
                        pageurl: window.location.href,
                        data: b.cData,
                        pagedata: b.chlPageData,
                        action: b.action,
                        userAgent: navigator.userAgent
                    };
                    console.log('intercepted-params:' + JSON.stringify(params));
                    window.cfCallback = b.callback;
                };
            }
        }, 50);
        """

        def console_handler(msg):
            if "intercepted-params:" in msg.text:
                try:
                    json_str = msg.text.split('intercepted-params:', 1)[1].strip()
                    self.captured_params = json.loads(json_str)
                except Exception as e:
                    print(f"[-] JSON Parse Error: {e}")

        self.console_listener = lambda msg: console_handler(msg)
        self.page.on("console", self.console_listener)

        retries = 5
        while retries > 0 and not self.captured_params:
            await self.page.reload()
            await self.page.evaluate(intercept_script)
            await asyncio.sleep(3)                                    
            retries -= 1

        self.page.remove_listener("console", self.console_listener)
        return self.captured_params

    async def solve_captcha_async(self, params):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.solve_captcha_sync, params)
    
    def solve_captcha_sync(self, params):
        solver = TwoCaptcha(self.api_key)
        try:
            result = solver.turnstile(
                sitekey=params["sitekey"],
                url=params["pageurl"],
                action=params["action"],
                data=params["data"],
                pagedata=params["pagedata"],
                useragent=params["userAgent"]
            )
            return result["code"]
        except Exception as e:
            async_to_sync(self.log.emit)("critical",f"[-] 2Captcha Error: {str(e).split('—')[-1].strip()}")
            return None

    async def send_token(self, token):
        await self.page.evaluate(f"""() => {{
            if (typeof window.cfCallback === 'function') {{
                window.cfCallback("{token}");
            }}
        }}""")
    
