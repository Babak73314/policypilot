"""README screenshots and the demo video, recorded from a headless browser.

Only page content is captured, from a headless Chromium driven by Playwright: never the screen,
a desktop or a browser window, so no address bar, taskbar or other window can appear. The video
is made of frames the headless browser renders of the page, captured continuously with their
times and assembled by ffmpeg; model waits and page loads are cut out.

Needs:
- a freshly seeded local stack with `ANTHROPIC_API_KEY` set, so Claude answers (live API):
  `docker compose down -v && docker compose up --build -d --wait`;
- Playwright for Python, installed outside this project's dependencies, for example:
  `python -m venv ~/.policypilot-capture`, then `pip install playwright` and
  `python -m playwright install chromium` in it;
- Docker, for ffmpeg (video only, image `jrottenberg/ffmpeg:7.1-alpine`); nothing is installed.

Usage (from the repository root, with that environment's Python):
    python scripts/record_demo.py screenshots   # docs/images/*.jpg, 1280 px wide at 2x
    python scripts/record_demo.py video         # docs/media/demo.mp4 and demo-preview.gif

Each mode creates conversations, so reset the stack before each run. The agent desk sign-in uses
SEED_AGENT_EMAIL and SEED_AGENT_PASSWORD from .env, happens off camera, and is never printed.
All data is fictional.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import Page, async_playwright

REPO = Path(__file__).resolve().parents[1]
BASE = "http://localhost:8080"
IMAGES = REPO / "docs" / "images"
MEDIA = REPO / "docs" / "media"
WORK = REPO / ".data" / "demo"  # git-ignored
FFMPEG = "jrottenberg/ffmpeg:7.1-alpine"
TYPING_DELAY_MS = 28

QUOTE = "I'm 40 and want $500,000 of term life cover for 20 years, budget around $80 a month"
POLICY = "What happens on the Kestrel policy if I miss a payment?"
APPLY = "I'd like to apply for Kestrel Term Life 20"
CONTACT = "My name is Jordan Sample and my email is jordan.sample@example.com"
HANDOFF = (
    "I'm 38 with two kids and need health insurance in the north region. Can I talk to a person?"
)
AUTO = "Car insurance please: I'm 30, I drive a sedan, about 12,000 miles a year, no claims"
NOTE = "Thanks, Jordan. I will email you the next steps."

# On-page overlays for the video: a title card and a caption per scene. Defined on every page
# load; nothing is sent anywhere.
OVERLAY_JS = """
window.ppOverlay = {
  caption(text) {
    let el = document.getElementById('pp-caption');
    if (!el) {
      el = document.createElement('div');
      el.id = 'pp-caption';
      el.style.cssText = 'position:fixed;left:50%;bottom:22px;transform:translateX(-50%);' +
        'z-index:2147483647;background:rgba(15,23,42,.88);color:#fff;font:600 19px/1.3 ' +
        'system-ui,sans-serif;padding:10px 22px;border-radius:999px;box-shadow:0 6px 24px ' +
        'rgba(0,0,0,.25);pointer-events:none;white-space:nowrap';
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.style.display = text ? 'block' : 'none';
  },
  title(lines) {
    let el = document.getElementById('pp-title');
    if (!lines) { if (el) el.remove(); return; }
    el = document.createElement('div');
    el.id = 'pp-title';
    el.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:flex;flex-direction:' +
      'column;align-items:center;justify-content:center;gap:14px;background:#0b3b3c;color:#fff;' +
      'font-family:system-ui,sans-serif;text-align:center';
    const sizes = ['700 56px', '400 26px', '400 18px'];
    lines.forEach((line, i) => {
      const p = document.createElement('p');
      p.textContent = line;
      p.style.cssText = `margin:0;font:${sizes[i] || sizes[2]}/1.3 system-ui,sans-serif;` +
        (i === 2 ? 'opacity:.8' : '');
      el.appendChild(p);
    });
    document.body.appendChild(el);
  },
};
"""


def dotenv(name: str) -> str:
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{name} is not set in .env")


class Recorder:
    """Captures the page continuously, frame by frame, with times; paused time is cut.

    Frames come from the headless page itself (`page.screenshot`, 1920x1080 at 1.5x): only page
    content, at full resolution. Chromium's CDP screencast was tried first, but it sends frames
    at CSS-pixel size (1280x720) whatever the device scale, which made the text soft.
    """

    def __init__(self, page: Page, frames: Path) -> None:
        self.page = page
        self.frames = frames
        self.items: list[tuple[float, Path]] = []
        self.start = 0.0
        self.cut = 0.0
        self.paused_at: float | None = None
        self.stopped = False
        self.task: asyncio.Task[None] | None = None

    def now(self) -> float:
        return time.time() - self.start - self.cut

    def _save(self, data: bytes, at: float) -> None:
        path = self.frames / f"{len(self.items):06d}.jpg"
        path.write_bytes(data)
        self.items.append((at, path))

    async def _capture(self) -> None:
        while not self.stopped:
            if self.paused_at is not None:
                await asyncio.sleep(0.05)
                continue
            at = self.now()
            data = await self.page.screenshot(type="jpeg", quality=90, caret="initial")
            if self.paused_at is None and not self.stopped:  # drop frames that straddle a cut
                self._save(data, at)

    async def begin(self) -> None:
        self.start = time.time()
        await self.snapshot()
        self.task = asyncio.create_task(self._capture())

    async def snapshot(self) -> None:
        """A frame of what is on screen now."""
        at = self.now()
        self._save(await self.page.screenshot(type="jpeg", quality=90, caret="initial"), at)

    def pause(self) -> None:
        if self.paused_at is None:
            self.paused_at = time.time()

    async def resume(self) -> None:
        if self.paused_at is not None:
            self.cut += time.time() - self.paused_at
            self.paused_at = None
        await self.snapshot()

    async def finish(self, hold: float) -> None:
        await self.snapshot()
        self.stopped = True
        if self.task:
            await self.task
        self.items.append((self.now() + hold, self.items[-1][1]))


class Demo:
    def __init__(self, page: Page, recorder: Recorder | None = None) -> None:
        self.page = page
        self.rec = recorder
        self.caption_text = ""

    async def hold(self, seconds: float) -> None:
        await self.page.wait_for_timeout(seconds * 1000)

    async def caption(self, text: str) -> None:
        self.caption_text = text
        if self.rec:
            await self.page.evaluate("t => window.ppOverlay.caption(t)", text)

    async def goto(self, path: str, ready: str) -> None:
        """Navigate off camera: no blank or half-loaded frames."""
        if self.rec:
            self.rec.pause()
        await self.page.goto(BASE + path)
        await self.page.wait_for_selector(ready, timeout=30_000)
        await self.page.wait_for_load_state("networkidle")
        if self.rec:
            await self.page.evaluate("t => window.ppOverlay.caption(t)", self.caption_text)
            await self.rec.resume()

    async def type(self, selector: str, text: str) -> None:
        field = self.page.locator(selector)
        await field.click()
        if self.rec:
            await field.press_sequentially(text, delay=TYPING_DELAY_MS)
        else:
            await field.fill(text)

    async def answered(self, before: int, wait_for: str | None = None) -> None:
        """Wait for the assistant's reply; the wait is cut from the video after a second."""
        cut_after = time.time() + 1.0
        while True:
            done = await self.page.evaluate(
                """([n, needle]) => {
                  const ol = document.querySelector('ol[aria-label="Conversation"]');
                  const text = document.querySelector('main')?.innerText || '';
                  // "Start over" is disabled exactly while a turn is pending (quote cards can
                  // arrive before the reply text, so the cards alone do not mean done).
                  const restart = [...document.querySelectorAll('button')]
                    .find(b => b.textContent.trim() === 'Start over');
                  return !!ol && ol.querySelectorAll(':scope > li').length >= n
                    && !text.includes('Thinking…') && !!restart && !restart.disabled
                    && (!needle || text.includes(needle));
                }""",
                [before + 2, wait_for],
            )
            if done:
                break
            if self.rec and time.time() > cut_after:
                self.rec.pause()
            await self.page.wait_for_timeout(250)
        await self.page.wait_for_timeout(400)  # let the cards finish rendering
        if self.rec:
            await self.rec.resume()

    async def say(self, text: str, wait_for: str | None = None) -> None:
        before = await self.page.locator('ol[aria-label="Conversation"] > li').count()
        await self.type('footer textarea, footer input[type="text"]', text)
        await self.page.get_by_role("button", name="Send").click()
        await self.answered(before, wait_for)

    async def click_reply(self, name: str, wait_for: str) -> None:
        before = await self.page.locator('ol[aria-label="Conversation"] > li').count()
        await self.page.get_by_role("button", name=name, exact=True).last.click()
        await self.answered(before, wait_for)

    async def scroll_bottom(self) -> None:
        await self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        await self.hold(0.4)

    async def expand_sources(self) -> None:
        await self.page.get_by_text("sources from the policy wording").last.click()
        await self.scroll_bottom()

    async def sign_in(self) -> None:
        """Off camera; the password is typed into a password field and never printed."""
        if self.rec:
            self.rec.pause()
        await self.page.goto(BASE + "/agent/login")
        await self.page.get_by_label("Email").fill(dotenv("SEED_AGENT_EMAIL"))
        await self.page.get_by_label("Password").fill(dotenv("SEED_AGENT_PASSWORD"))
        await self.page.get_by_role("button", name="Sign in").click()
        await self.page.wait_for_url(BASE + "/agent", timeout=15_000)

    async def whatsapp(self) -> None:
        await self.goto("/simulator", 'input[placeholder="Message"]')
        field = self.page.locator('input[placeholder="Message"]')
        await field.click()
        if self.rec:
            await field.press_sequentially(AUTO, delay=TYPING_DELAY_MS)
        else:
            await field.fill(AUTO)
        await field.press("Enter")
        cut_after = time.time() + 1.0
        phone = self.page.get_by_label("Simulated phone")
        while "per year" not in (await phone.inner_text()):
            if self.rec and time.time() > cut_after:
                self.rec.pause()
            await self.page.wait_for_timeout(250)
        await self.page.wait_for_timeout(1500)  # the reply may come in more than one message
        if self.rec:
            await self.rec.resume()

    async def scroll_phone(self, top: int) -> None:
        await self.page.evaluate(
            """top => { const list = [...document.querySelectorAll('*')].find(e =>
                 e.scrollHeight > e.clientHeight + 20 && /auto|scroll/.test(
                 getComputedStyle(e).overflowY)); if (list) list.scrollTo({top, behavior:
                 'smooth'}); }""",
            top,
        )
        await self.hold(0.8)

    async def open_runs(self) -> None:
        await self.page.get_by_text("product_question", exact=True).first.click()
        await self.page.evaluate(
            """() => { const h = [...document.querySelectorAll('*')].find(e =>
                 e.childElementCount === 0 && /^assistant runs$/i.test(e.textContent.trim()));
                 if (h) { h.scrollIntoView({block: 'start'}); window.scrollBy(0, -16); } }"""
        )
        await self.hold(0.5)


async def screenshots(page: Page) -> None:
    """The four README screenshots, 1280 px wide at 2x (2560 px images)."""
    demo = Demo(page)
    shot = {"type": "jpeg", "quality": 86}
    await page.set_viewport_size({"width": 1280, "height": 1000})
    await page.goto(BASE + "/")
    await page.wait_for_selector('ol[aria-label="Conversation"]')
    await demo.say(QUOTE, wait_for="per month")
    await demo.say(POLICY, wait_for="[")
    await demo.expand_sources()
    await page.screenshot(path=IMAGES / "chat-quotes-and-citation.jpg", **shot)

    await demo.say(APPLY)
    await demo.say(CONTACT, wait_for="Please answer yes or no")
    await demo.click_reply("Yes", wait_for="Shall I submit")
    await demo.click_reply("Submit", wait_for="sent to a licensed agent")
    await page.get_by_role("button", name="Start over").click()
    await page.wait_for_timeout(1500)
    await demo.say(HANDOFF, wait_for="asked a licensed agent")

    await page.set_viewport_size({"width": 1280, "height": 860})
    await demo.whatsapp()
    await demo.scroll_phone(110)
    await page.screenshot(path=IMAGES / "whatsapp-simulator.jpg", **shot)

    await demo.sign_in()
    await page.set_viewport_size({"width": 1280, "height": 520})
    await demo.goto("/agent/reviews", "table")
    await page.screenshot(path=IMAGES / "review-queue.jpg", **shot)

    await page.set_viewport_size({"width": 1280, "height": 760})
    await demo.goto("/agent/conversations", "table")
    await page.get_by_role("link", name="Jordan Sample").click()
    await page.wait_for_selector("text=Assistant runs")
    await demo.open_runs()
    await page.screenshot(path=IMAGES / "run-trace.jpg", **shot)


async def video(page: Page) -> None:
    """The storyboard; captions are on-page overlays, model waits are cut."""
    WORK.mkdir(parents=True, exist_ok=True)
    frames = WORK / "frames"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir()
    rec = Recorder(page, frames)
    demo = Demo(page, rec)

    await page.goto(BASE + "/")
    await page.wait_for_selector('ol[aria-label="Conversation"]')
    await page.wait_for_load_state("networkidle")
    await page.evaluate(
        "l => window.ppOverlay.title(l)",
        [
            "PolicyPilot",
            "An AI copilot for a fictional multi-carrier insurance agency",
            "Live demo with Claude · prices from a rating engine · a person approves",
        ],
    )
    await rec.begin()
    await demo.hold(3.5)
    await page.evaluate("() => window.ppOverlay.title(null)")

    await demo.caption("1 · A customer asks for a quote")
    await demo.hold(0.8)
    await demo.say(QUOTE, wait_for="per month")
    await demo.scroll_bottom()
    await demo.hold(4.0)

    await demo.caption("2 · A policy question gets an answer cited from the wording")
    await demo.say(POLICY, wait_for="[")
    await demo.hold(1.0)
    await demo.expand_sources()
    await demo.hold(3.5)

    await demo.caption("3 · Applying: contact details and consent")
    await demo.say(APPLY)
    await demo.say(CONTACT, wait_for="Please answer yes or no")
    await demo.hold(1.0)
    await demo.click_reply("Yes", wait_for="Shall I submit")
    await demo.hold(2.0)
    await demo.click_reply("Submit", wait_for="sent to a licensed agent")
    await demo.hold(2.0)

    await demo.caption("4 · The agent desk: the application waits for review")
    await demo.sign_in()
    await demo.goto("/agent/reviews", "table")
    await demo.hold(2.5)
    await page.get_by_role("link", name="Application review").click()
    await page.wait_for_selector("text=Claim this review")
    await rec.snapshot()
    await demo.caption("5 · A licensed agent claims and approves it")
    await demo.hold(1.5)
    await page.get_by_role("button", name="Claim this review").click()
    note = page.get_by_label("Note to the customer")
    await note.wait_for()
    await demo.hold(0.8)
    await note.click()
    await note.press_sequentially(NOTE, delay=TYPING_DELAY_MS)
    await demo.hold(0.6)
    await page.get_by_role("button", name="Approve", exact=True).click()
    await page.wait_for_selector("text=Decision saved and sent to the customer.")
    await demo.hold(2.5)

    await demo.caption("6 · The decision arrives in the customer's chat")
    await demo.goto("/", 'ol[aria-label="Conversation"]')
    while "Good news" not in await page.locator("main").inner_text():
        rec.pause()
        await page.wait_for_timeout(500)
    await rec.resume()
    await demo.scroll_bottom()
    await demo.hold(4.0)

    await demo.caption("7 · Every run is traced: models, tools and the guardrail")
    await demo.goto("/agent/conversations", "table")
    rec.pause()
    await page.get_by_role("link", name="Jordan Sample").click()
    await page.wait_for_selector("text=Assistant runs")
    await page.evaluate("t => window.ppOverlay.caption(t)", demo.caption_text)
    await demo.open_runs()
    await rec.resume()
    await demo.hold(4.5)

    await demo.caption("8 · The same assistant on WhatsApp (local simulator)")
    await demo.whatsapp()
    await demo.hold(2.0)
    await demo.scroll_phone(110)
    await demo.hold(2.5)
    await demo.scroll_phone(700)
    await demo.hold(2.5)
    await demo.caption("")
    await page.evaluate(
        "l => window.ppOverlay.title(l)",
        ["PolicyPilot", "Open source (MIT) · all data in this demo is fictional"],
    )
    await rec.finish(hold=3.0)
    assemble(rec.items)


def ffmpeg(*args: str) -> None:
    command = ["docker", "run", "--rm", "-v", f"{WORK}:/work", "-w", "/work", FFMPEG, *args]
    subprocess.run(command, check=True)  # noqa: S603 - fixed arguments, local files only


def assemble(items: list[tuple[float, Path]]) -> None:
    """Frames with their times -> a 30 fps H.264 MP4, plus a short animated GIF preview."""
    items.sort(key=lambda item: item[0])
    lines = []
    for (at, path), (next_at, _) in itertools.pairwise(items):
        lines += [f"file 'frames/{path.name}'", f"duration {max(next_at - at, 1 / 60):.4f}"]
    lines.append(f"file 'frames/{items[-1][1].name}'")
    (WORK / "frames.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(items)} frames, {items[-1][0]:.1f} s")
    ffmpeg(
        "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "frames.txt",
        "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-movflags", "+faststart",
        "demo.mp4",
    )  # fmt: skip
    ffmpeg(
        "-y", "-loglevel", "error", "-ss", "3.5", "-t", "16", "-i", "demo.mp4",
        "-vf", "fps=8,scale=880:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96"
        "[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
        "demo-preview.gif",
    )  # fmt: skip
    MEDIA.mkdir(parents=True, exist_ok=True)
    for name in ("demo.mp4", "demo-preview.gif"):
        shutil.copyfile(WORK / name, MEDIA / name)
        print(f"{MEDIA / name}: {(MEDIA / name).stat().st_size / 1_000_000:.2f} MB")


async def main(mode: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        scale = 2 if mode == "screenshots" else 1.5
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720}, device_scale_factor=scale, locale="en-US"
        )
        await context.add_init_script(OVERLAY_JS)
        page = await context.new_page()
        try:
            await (screenshots(page) if mode == "screenshots" else video(page))
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("mode", choices=["screenshots", "video"])
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main(parser.parse_args().mode))
