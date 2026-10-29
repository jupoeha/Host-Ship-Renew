#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Host-Ship 免费服自动续期 (SeleniumBase UC 模式, 过 Cloudflare Turnstile)
参照上传的 hostship_renew.py: 流程、环境变量、TG 文案保持一致。

Host-Ship 面板 (Pterodactyl) 登录页开了 Cloudflare Turnstile (siteKey 0x4AAAAAACXpBx25R_J92QJ2),
原脚本用无头 Playwright 遇到验证就放弃。这里改成 UC 模式 + 系统级真实鼠标点击过盾,
过不去时可选打码平台拿 token, 再直接调用面板的 /auth/login 接口登录。

安装:
  apt install -y xvfb && pip install -U seleniumbase pyautogui python-xlib pysocks
  (Linux 自动开虚拟屏; 需要 Google Chrome)

环境变量:
  SERVER_URL          服务器详情页, 如 https://panel.host-ship.com/server/abcd1234   必填
  HOSTSHIP_LOGIN      登录邮箱/用户名   必填
  HOSTSHIP_PASSWORD   登录密码         必填
  TG_BOT_TOKEN / TG_CHAT_ID           Telegram 通知 (可选)
  NODE_LINK           填写即启用代理 (浏览器 + TG/打码请求共用)，不填直连
  PROXY_SERVER        默认 socks5://127.0.0.1:1080
  MANUAL_RUN          true 时未到续期时间也发 TG 通知
  CF_TIMEOUT          自己过盾最多等待秒数, 默认 60
  CONFIRM_TIMEOUT     等确认弹窗按钮出现并可点击的最多秒数, 默认 30
  CAPTCHA_PROVIDER    打码兜底: capsolver 或 2captcha (可选)
  CAPTCHA_KEY         打码平台 API Key
"""
import os
import re
import sys
import json
import time
import random
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from seleniumbase import SB

SERVER_URL = os.getenv("SERVER_URL", "").strip()
HOSTSHIP_LOGIN = os.getenv("HOSTSHIP_LOGIN", "").strip()
HOSTSHIP_PASSWORD = os.getenv("HOSTSHIP_PASSWORD", "").strip()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()

NODE_LINK = (os.getenv("NODE_LINK") or "").strip()
IS_PROXY = bool(NODE_LINK)
PROXY_SERVER = (os.getenv("PROXY_SERVER") or "socks5://127.0.0.1:1080").strip()

MANUAL_RUN = os.getenv("MANUAL_RUN", "false").lower() == "true"
CF_TIMEOUT = int(os.getenv("CF_TIMEOUT", "60"))
CONFIRM_TIMEOUT = int(os.getenv("CONFIRM_TIMEOUT", "30"))
CAPTCHA_PROVIDER = os.getenv("CAPTCHA_PROVIDER", "").strip().lower()
CAPTCHA_KEY = os.getenv("CAPTCHA_KEY", "").strip()

PANEL = "https://panel.host-ship.com"
LOGIN_URL = PANEL + "/auth/login"
BJ_TZ = ZoneInfo("Asia/Shanghai")
SCHEDULE_TEXT = "每周五 15:28（北京时间）"


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ======================== 网络 (支持代理) ========================

def build_opener():
    if not IS_PROXY:
        return urllib.request.build_opener()
    u = urllib.parse.urlparse(PROXY_SERVER)
    if u.scheme.startswith("socks"):
        try:
            import socks
            from sockshandler import SocksiPyHandler
        except ImportError:
            log("⚠️ 未安装 pysocks, 脚本请求将直连 (浏览器仍走代理)")
            return urllib.request.build_opener()
        ptype = socks.SOCKS4 if u.scheme == "socks4" else socks.SOCKS5
        return urllib.request.build_opener(
            SocksiPyHandler(ptype, u.hostname, u.port or 1080, True, u.username, u.password))
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY_SERVER, "https": PROXY_SERVER}))


OPENER = build_opener()


def urlopen(req, timeout=30):
    return OPENER.open(req, timeout=timeout)


def http_json(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urlopen(req).read().decode())


# ======================== 文案 (同原脚本) ========================

def server_id():
    return SERVER_URL.rstrip("/").split("/")[-1] if SERVER_URL else "未知"


def node_status():
    return "✅ 已启用" if IS_PROXY else "⚪ 未启用（直连）"


def get_days(text):
    m = re.search(r"(\d+)\s*Days?", text or "", re.I)
    return int(m.group(1)) if m else None


def beijing_now():
    return datetime.now(BJ_TZ)


def build_check_message(status, ip):
    days = get_days(status)
    lines = [
        "⏳ Host-Ship 检查完成", "",
        f"🖥️ 服务器：#{server_id()}",
        f"🌐 节点状态：{node_status()}",
        f"📍 出口IP：{ip}",
        f"🕗 检查时间：{beijing_now().strftime('%Y/%m/%d %H:%M')}", "",
        "🔒 当前状态：未到续期时间",
    ]
    if days is not None:
        lines.append(f"⏱️ 距离续期：约 {days} 天")
        lines.append("📆 预计可续期：" + (beijing_now().date() + timedelta(days=days)).strftime("%Y/%m/%d"))
    else:
        lines.append(f"📅 面板状态：{status}")
    lines.append(f"⏰ 自动检查：{SCHEDULE_TEXT}")
    return "\n".join(lines)


def build_success_message(before, after, ip):
    bd, ad = get_days(before), get_days(after)
    return "\n".join([
        "🎉 Host-Ship 续期成功", "",
        f"🖥️ 服务器：#{server_id()}",
        f"🌐 节点状态：{node_status()}",
        f"📍 出口IP：{ip}",
        f"🕗 续期时间：{beijing_now().strftime('%Y/%m/%d %H:%M')}", "",
        f"📅 续期前：约 {bd} 天" if bd is not None else f"📅 续期前：{before}",
        f"✅ 续期后：约 {ad} 天" if ad is not None else f"✅ 续期后：{after}",
        f"⏰ 自动检查：{SCHEDULE_TEXT}",
    ])


def build_error_message(title, reason, ip):
    return (f"{title}\n\n"
            f"🖥️ 服务器：#{server_id()}\n"
            f"🌐 节点状态：{node_status()}\n"
            f"📍 出口IP：{ip}\n"
            f"🕗 检查时间：{beijing_now().strftime('%Y/%m/%d %H:%M')}\n\n"
            f"⚠️ 原因：{reason}")


def tg(text, image_path=None):
    """发送 TG 通知, 有截图时以图片+说明发送"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False
    try:
        base = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"
        if image_path and os.path.exists(image_path):
            boundary = "----hs" + str(random.randint(10**8, 10**9))
            with open(image_path, "rb") as f:
                img = f.read()
            body = b""
            for k, v in (("chat_id", TG_CHAT_ID), ("caption", text[:1024])):
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
                     f'filename="{os.path.basename(image_path)}"\r\nContent-Type: image/png\r\n\r\n').encode()
            body += img + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(base + "/sendPhoto", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        else:
            req = urllib.request.Request(base + "/sendMessage",
                                         data=json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode(),
                                         headers={"Content-Type": "application/json"})
        urlopen(req, timeout=30).read()
        log("✅ Telegram 通知发送成功")
        return True
    except Exception as exc:
        log(f"❌ Telegram 通知异常: {exc}")
        return False


def current_ip():
    try:
        return urlopen("https://api.ipify.org", timeout=15).read().decode().strip()
    except Exception:
        return "获取失败"


def shot(sb, name):
    try:
        sb.save_screenshot(name)
        return name
    except Exception:
        return None


# ======================== Cloudflare Turnstile ========================

JS_CF_STATE = r'''
var t = document.title.toLowerCase();
if (t.indexOf('just a moment') >= 0 || document.querySelector('#challenge-stage,#challenge-running')) return 'interstitial';
var frame = [...document.querySelectorAll('iframe')].some(f => (f.src || '').indexOf('challenges.cloudflare.com') >= 0);
var ins = document.querySelectorAll('input[name="cf-turnstile-response"]');
if (!frame && !ins.length && !document.querySelector('.cf-turnstile,[data-sitekey]')) return 'none';
if (!ins.length) return 'pending';
for (var i = 0; i < ins.length; i++) { if (!ins[i].value || ins[i].value.length < 20) return 'pending'; }
return 'ok';
'''


def cf_state(sb):
    try:
        return sb.execute_script(JS_CF_STATE)
    except Exception:
        return "pending"


def solve_token_by_provider(url):
    """打码平台拿 Turnstile token"""
    if not CAPTCHA_PROVIDER or not CAPTCHA_KEY:
        return ""
    sitekey = "0x4AAAAAACXpBx25R_J92QJ2"
    log(f"🧩 调用 {CAPTCHA_PROVIDER} 打码...")
    if CAPTCHA_PROVIDER == "capsolver":
        api, task = "https://api.capsolver.com", {"type": "AntiTurnstileTaskProxyLess", "websiteURL": url, "websiteKey": sitekey}
    elif CAPTCHA_PROVIDER == "2captcha":
        api, task = "https://api.2captcha.com", {"type": "TurnstileTaskProxyless", "websiteURL": url, "websiteKey": sitekey}
    else:
        log(f"⚠️ 不支持的 CAPTCHA_PROVIDER: {CAPTCHA_PROVIDER}")
        return ""
    created = http_json(api + "/createTask", {"clientKey": CAPTCHA_KEY, "task": task})
    if not created.get("taskId"):
        raise RuntimeError(f"打码创建任务失败: {created}")
    for _ in range(40):
        time.sleep(3)
        r = http_json(api + "/getTaskResult", {"clientKey": CAPTCHA_KEY, "taskId": created["taskId"]})
        if r.get("status") == "ready":
            return (r.get("solution") or {}).get("token", "")
        if r.get("errorId"):
            raise RuntimeError(f"打码失败: {r}")
    raise RuntimeError("打码超时")


def wait_turnstile(sb, label=""):
    """等 Turnstile 通过; 返回 True=已通过/没有盾, False=超时"""
    start, tries, announced = time.time(), 0, False
    while time.time() - start < CF_TIMEOUT:
        st = cf_state(sb)
        if st in ("none", "ok"):
            if announced:
                log(f"🛡️ Cloudflare 已通过 {label} ({time.time() - start:.1f}s)")
            return True
        if not announced:
            log(f"🛡️ 检测到 Cloudflare {label} ({st})，处理中...")
            announced = True
        sb.sleep(3)
        if cf_state(sb) in ("none", "ok"):
            continue
        tries += 1
        try:
            log(f"   🖱️ 系统级点击验证框 (第 {tries} 次)")
            sb.uc_gui_click_captcha()
        except Exception as e:
            log(f"   点击失败: {e}")
        sb.sleep(4)
        if tries == 3 and st == "interstitial":
            sb.uc_open_with_reconnect(sb.get_current_url(), 6)
    return False


# ======================== 登录 ========================

def first_visible(sb, selectors):
    for s in selectors:
        try:
            if sb.is_element_visible(s):
                return s
        except Exception:
            pass
    return None


def on_login_page(sb):
    return "/auth/login" in sb.get_current_url()


JS_API_LOGIN = r'''
var done = arguments[arguments.length - 1];
var user = arguments[0], pass = arguments[1], token = arguments[2];
function cookie(n){ var m = document.cookie.match('(^|;)\\s*' + n + '=([^;]*)'); return m ? decodeURIComponent(m[2]) : ''; }
fetch('/sanctum/csrf-cookie', {credentials: 'include'}).then(function(){
  return fetch('/auth/login', {
    method: 'POST', credentials: 'include',
    headers: {'Content-Type': 'application/json', 'Accept': 'application/json',
              'X-Requested-With': 'XMLHttpRequest', 'X-XSRF-TOKEN': cookie('XSRF-TOKEN')},
    body: JSON.stringify({user: user, password: pass, 'g-recaptcha-response': token, 'cf-turnstile-response': token})
  });
}).then(function(r){ return r.text().then(function(t){ done(r.status + ' ' + t.slice(0, 300)); }); })
  .catch(function(e){ done('ERR ' + e); });
'''


def login(sb):
    sb.uc_open_with_reconnect(SERVER_URL, 5)
    sb.sleep(3)
    if "/server/" in sb.get_current_url() and not on_login_page(sb):
        return True, ""

    if not on_login_page(sb):
        sb.uc_open_with_reconnect(LOGIN_URL, 5)
        sb.sleep(3)
    if cf_state(sb) == "interstitial":
        wait_turnstile(sb, "[打开登录页]")

    email = first_visible(sb, ['input[name="username"]', 'input[name="email"]', 'input[name="user"]',
                               'input[type="email"]', 'input[autocomplete="username"]'])
    password = first_visible(sb, ['input[name="password"]', 'input[type="password"]'])
    if not email or not password:
        return False, f"没找到登录框，当前页面: {sb.get_current_url()}"
    if not HOSTSHIP_LOGIN or not HOSTSHIP_PASSWORD:
        return False, "缺少 HOSTSHIP_LOGIN / HOSTSHIP_PASSWORD"

    log("🔐 正在登录 Host-Ship...")
    sb.type(email, HOSTSHIP_LOGIN)
    sb.sleep(random.uniform(0.4, 1.0))
    sb.type(password, HOSTSHIP_PASSWORD)
    sb.sleep(random.uniform(0.4, 1.0))

    if wait_turnstile(sb, "[登录]"):
        submit = first_visible(sb, ['button[type="submit"]', 'button:contains("Login")',
                                    'button:contains("Sign in")', 'button:contains("Log in")'])
        if not submit:
            return False, "没找到登录按钮"
        sb.click(submit)
        for _ in range(20):
            sb.sleep(1)
            if not on_login_page(sb):
                break
    else:
        # 自己过不去: 打码拿 token, 直接调面板登录接口
        token = solve_token_by_provider(sb.get_current_url())
        if not token:
            return False, "Cloudflare 验证超时 (可配置 CAPTCHA_PROVIDER / CAPTCHA_KEY 兜底)"
        sb.driver.set_script_timeout(30)
        res = sb.driver.execute_async_script(JS_API_LOGIN, HOSTSHIP_LOGIN, HOSTSHIP_PASSWORD, token)
        log(f"🧩 接口登录返回: {res[:120]}")

    if on_login_page(sb) or "/server/" not in sb.get_current_url():
        sb.uc_open_with_reconnect(SERVER_URL, 4)
        sb.sleep(3)
    if on_login_page(sb):
        try:
            err = sb.get_text('[role="alert"], .text-red-500, [class*="error"]', timeout=1)
        except Exception:
            err = ""
        return False, "登录失败" + (f": {err}" if err else "（账号密码错误或安全验证未通过）")
    return "/server/" in sb.get_current_url(), ""


# ======================== 续期 ========================

def body_text(sb):
    try:
        return sb.get_text("body")
    except Exception:
        return ""


def get_renewal_text(sb):
    # 旧代码只认 "Renewal in X Days / Renew in X Days / X Days until renewal" 三种,
    # 面板实际文案只要是 Expires in / days left / 到期日期等, 续期前后都会是 "未识别",
    # 哪怕 Add 3 days 点成功了也判不成。放宽到天数和日期两类, 并加兜底行。
    text = body_text(sb)
    flat = re.sub(r"\s+", " ", text)
    for p in (
        r"Renewal\s+in\s+\d+\s+Days?",
        r"Renew\s+in\s+\d+\s+Days?",
        r"\d+\s+Days?\s+until\s+renewal",
        r"Expires?\s+in\s+\d+\s+Days?",
        r"Expir(?:y|ation)\s*(?:in|:)?\s*\d+\s+Days?",
        r"\d+\s+Days?\s+(?:left|remaining)",
        r"(?:Days?\s+left|Time\s+left|Remaining)[^0-9]{0,20}\d+\s+Days?",
        r"Renewal[^.]{0,40}?\d{4}[-/]\d{2}[-/]\d{2}",
        r"Expires?[^.]{0,40}?\d{4}[-/]\d{2}[-/]\d{2}",
        r"Expiry[^.]{0,40}?\d{4}[-/]\d{2}[-/]\d{2}",
    ):
        m = re.search(p, flat, re.I)
        if m:
            return m.group(0).strip()
    if re.search(r"Renew\s+Limit\s+Reached", text, re.I):
        return "Renew Limit Reached"
    # 兜底: 含数字且含 renew/expir 的行 (排除纯按钮文案, 避免把 "Add 3 days" 当成剩余天数)
    for line in text.splitlines():
        s = line.strip()
        if s and re.search(r"\d", s) and re.search(r"renew|expir", s, re.I) \
                and s.lower() not in ("renew", "add 3 days", "renew now"):
            return s[:120]
    return "未识别"


def renewal_snippet(sb):
    """失败诊断用: 页面上含 renew/expir 且含数字的相关行, 最多 3 行。

    下次再出现未识别, TG 里直接带这段, 不用只靠 before/after 猜实际文案。
    """
    out = []
    for line in body_text(sb).splitlines():
        s = re.sub(r"\s+", " ", line).strip()
        if s and re.search(r"\d", s) and re.search(r"renew|expir", s, re.I) \
                and s.lower() not in ("renew", "add 3 days", "renew now"):
            if s not in out:
                out.append(s[:120])
        if len(out) >= 3:
            break
    return "；".join(out)


RENEW_XP = ("//*[self::button or self::a][normalize-space()='Renew' or normalize-space()='Renew Now' "
            "or normalize-space()='Renew Server' or normalize-space()='renew']")
# 确认按钮按优先级匹配: 弹窗内精确 -> 全局精确 -> 弹窗内 Renew/Confirm 包含
# 旧代码只查一次、且按钮一开始是 disabled 就直接判失败, 弹窗慢 / 按钮先禁后放
# / 文案不是精确 "Renew now" 时都会走到 "未能点击确认弹窗"。
# 实测诊断证明: 点第一层 Renew 后页面并没有 Renew now, 弹窗尾部出现的是
# Cancel / Add 3 days, 所以 Add 3 days 才是这个面板真正的确认按钮, 放最前面。
CONFIRM_XPS = [
    "//*[@role='dialog' or contains(@class,'modal')]//*[self::button or self::a][normalize-space()='Add 3 days']",
    "//*[self::button or self::a][normalize-space()='Add 3 days']",
    "//*[@role='dialog' or contains(@class,'modal')]//button[normalize-space()='Renew now' or normalize-space()='Renew Now']",
    "//button[normalize-space()='Renew now' or normalize-space()='Renew Now']",
    "//*[@role='dialog' or contains(@class,'modal')]//*[self::button or self::a][normalize-space()='Renew now' or normalize-space()='Renew Now']",
    "//*[@role='dialog' or contains(@class,'modal')]//*[self::button or self::a][contains(normalize-space(),'Renew')]",
    "//*[@role='dialog' or contains(@class,'modal')]//*[self::button or self::a][contains(normalize-space(),'Confirm')]",
    "//*[@role='dialog' or contains(@class,'modal')]//*[self::button or self::a][contains(normalize-space(),'Add')]",
]


def find_visible(sb, xpath):
    try:
        for el in sb.find_elements(xpath, by="xpath"):
            if el.is_displayed():
                return el
    except Exception:
        pass
    return None


def is_disabled(el):
    try:
        return (not el.is_enabled()) or el.get_attribute("disabled") is not None \
            or el.get_attribute("aria-disabled") == "true"
    except Exception:
        return False


def visible_buttons_text(sb):
    """失败诊断用: 当前页面所有可见按钮/a 的文案, 下次失败时就知道弹窗按钮到底叫什么。

    只取每个元素的首行并截断到 40 字: 卡片式 <a> 的 innerText 会带整段广告文案,
    不截断会把 TG 诊断撑爆 (上次 Premium 卡片整段都进来了)。
    """
    try:
        return sb.execute_script(
            "return [...document.querySelectorAll('button,a')]"
            ".filter(e=>e.offsetParent!==null)"
            ".map(e=>((e.innerText||'').trim().split('\\n')[0]||'').trim().slice(0,40))"
            ".filter(Boolean).slice(0,40);")
    except Exception:
        return []


def wait_for_confirm_button(sb, timeout=None):
    """轮询等确认按钮出现且可点击。

    返回 (元素或 None, 诊断信息): 按钮出现过但一直 disabled、或一直没出现,
    都等满 timeout 才判失败, 不再像旧代码那样只查一次就报错。
    """
    timeout = CONFIRM_TIMEOUT if timeout is None else timeout
    deadline, saw_disabled = time.time() + timeout, False
    while time.time() < deadline:
        for xp in CONFIRM_XPS:
            el = find_visible(sb, xp)
            if el:
                if is_disabled(el):
                    saw_disabled = True
                    break
                return el, ""
        sb.sleep(1)
    diag = "按钮出现过但一直不可点击" if saw_disabled else "超时内没出现确认按钮"
    btns = visible_buttons_text(sb)
    if btns:
        diag += "；页面可见按钮：" + " / ".join(btns[:25])
    return None, diag


def _extract_date(s):
    m = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", s or "")
    if not m:
        return None
    try:
        from datetime import date as _date
        return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def renewal_succeeded(before, after, text):
    t = text.lower()
    if any(w in t for w in ("renewed successfully", "renewal successful", "successfully renewed",
                            "renew limit reached", "server renewed", "renewal complete",
                            "successfully extended", "days added", "added 3 days",
                            "extension successful", "renewed for")):
        return True
    b, a = get_days(before), get_days(after)
    if b is not None and a is not None and a > b:
        return True
    # 到期日期型文案: 日期变晚也算成功
    db, da = _extract_date(before), _extract_date(after)
    return db is not None and da is not None and da > db


def wait_for_renewal_result(sb, before):
    after = get_renewal_text(sb)
    for attempt in range(8):
        after = get_renewal_text(sb)
        if renewal_succeeded(before, after, body_text(sb)):
            return True, after
        sb.sleep(2)
        if attempt == 1:
            sb.refresh()
            sb.sleep(3)
    return False, after


def run(sb, ip):
    ok, reason = login(sb)
    if not ok:
        p = shot(sb, "hostship_login_fail.png")
        tg(build_error_message("❌ Host-Ship 登录失败", reason or "登录失败或遇到安全验证", ip), p)
        return 1
    log("✅ 登录成功")
    sb.sleep(3)

    before = get_renewal_text(sb)
    log(f"📅 当前续期状态：{before}")

    if re.search(r"Renew\s+Limit\s+Reached", body_text(sb), re.I):
        log("⏳ 目前未到续期时间，不进行操作")
        if MANUAL_RUN:
            tg(build_check_message(before, ip))
        return 0

    button = find_visible(sb, RENEW_XP)
    if not button:
        p = shot(sb, "hostship_no_renew_button.png")
        tg(build_error_message("⚠️ Host-Ship 需要检查", f"没有找到可用的 Renew 按钮；{before}", ip), p)
        return 1

    if is_disabled(button):
        log("⏳ Renew 按钮当前不可点击")
        if MANUAL_RUN:
            tg(build_check_message(before, ip))
        return 0

    log("🔄 已到续期窗口，点击 Renew...")
    button.click()

    # 确认弹窗 (可能也带 Turnstile): 先轮询等弹窗+按钮可用, 再过盾, 过盾后再等一次
    # (盾可能让按钮先禁后放; 旧代码只查一次, 弹窗慢或按钮先 disabled 就直接报失败)
    confirm, diag = wait_for_confirm_button(sb)
    if not confirm and cf_state(sb) not in ("none", "ok"):
        if not wait_turnstile(sb, "[续期确认]"):
            p = shot(sb, "hostship_confirm_fail.png")
            tg(build_error_message("❌ Host-Ship 续期确认失败", "确认弹窗的 Cloudflare 验证未通过", ip), p)
            return 1
        confirm, diag = wait_for_confirm_button(sb, timeout=15)
    if not confirm:
        log(f"❌ 确认弹窗失败: {diag}")
        p = shot(sb, "hostship_confirm_fail.png")
        tg(build_error_message("❌ Host-Ship 续期确认失败",
                               f"点击第一层 Renew 后，未能点击确认弹窗中的 Renew now；{diag}", ip), p)
        return 1
    log("✅ 确认弹窗已打开，点击确认按钮...")
    try:
        confirm.click()
    except Exception:
        # 元素被遮挡等导致普通点击失败时, 用 JS 点击兜底
        try:
            sb.execute_script("arguments[0].click();", confirm)
        except Exception as e:
            p = shot(sb, "hostship_confirm_fail.png")
            tg(build_error_message("❌ Host-Ship 续期确认失败", f"确认按钮已找到但点击失败: {e}", ip), p)
            return 1
    sb.sleep(3)

    success, after = wait_for_renewal_result(sb, before)
    if success:
        log(f"✅ 续期成功：{before} -> {after}")
        p = shot(sb, "hostship_success.png")
        tg(build_success_message(before, after, ip), p)
        return 0

    p = shot(sb, "hostship_renew_uncertain.png")
    log("⚠️ 已点击续期，但无法确认结果")
    snippet = renewal_snippet(sb)
    reason = f"续期前：{before}；续期后：{after}"
    if snippet:
        reason += f"；页面相关文案：{snippet}"
    tg(build_error_message("⚠️ Host-Ship 续期结果需要检查", reason, ip), p)
    return 1


def main():
    if not SERVER_URL.startswith(PANEL + "/server/"):
        log("❌ SERVER_URL 不正确")
        tg("❌ Host-Ship 配置错误\nSERVER_URL 不是服务器详情页地址")
        return 1

    log("======================================")
    log(" Host-Ship Free Auto Renew")
    log("======================================")
    log(f"🌐 节点状态：{node_status()}")
    ip = current_ip()
    log(f"📍 当前出口IP：{ip}")

    kw = dict(uc=True, test=True, locale="en")
    if sys.platform.startswith("linux"):
        kw["xvfb"] = True          # 有头模式 + 虚拟屏, 过盾必需
    if IS_PROXY and PROXY_SERVER:
        kw["proxy"] = PROXY_SERVER

    with SB(**kw) as sb:
        try:
            return run(sb, ip)
        except Exception as exc:
            log(f"❌ 运行异常：{exc}")
            p = shot(sb, "hostship_error.png")
            tg(build_error_message("❌ Host-Ship 自动续期异常", str(exc), ip), p)
            return 1


if __name__ == "__main__":
    sys.exit(main())
