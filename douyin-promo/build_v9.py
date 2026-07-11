#!/usr/bin/env python3
"""液环抖音 v9 — 加入开源截图 · 数字冲击 · 多巴胺 · 纯解说"""
import os, subprocess, math, json, random
from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
FRAMES = os.path.join(BASE, "frames_v9")
AUDIO = os.path.join(BASE, "audio")
OUTPUT = os.path.join(BASE, "output")
os.makedirs(FRAMES, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)

FF = "/opt/homebrew/bin/ffmpeg"
FP = "/opt/homebrew/bin/ffprobe"
TP = os.path.expanduser("~/.qwenpaw/venv/bin/python3")
FPS = 24

HPINK = (255, 20, 147); GREEN = (0, 255, 180); BLUE = (0, 180, 255)
LIME = (180, 255, 20); LEMON = (255, 240, 0); CORAL = (255, 100, 80)
PURPLE = (180, 60, 255); WHITE = (255, 255, 255); GRAY = (160, 160, 180)
BG = (10, 8, 18)

# 开源截图路径
GITHUB_SCREENSHOT = "/Users/feixubuke/.qwenpaw/workspaces/default/media/B8719A4C3452F1A0DF45A1F9A2203947.png"

def ft(s):
    for p in ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"]:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, s)
            except: pass
    return ImageFont.load_default()

def eo(t): return 1-(1-t)**3
def cl(v): return max(0, min(1, v))
def lr(a, b, t): return a+(b-a)*cl(t)

def _bg(): return Image.new("RGB", (1080, 1920), BG)
def _center(d, y, txt, f, c=WHITE):
    bb = d.textbbox((0,0), txt, font=f)
    w = bb[2]-bb[0]
    d.text(((1080-w)//2, y), txt, fill=c, font=f)

def _grad(d, c1, c2):
    for y in range(1920):
        r = int(lr(c1[0], c2[0], y/1920))
        g = int(lr(c1[1], c2[1], y/1920))
        b = int(lr(c1[2], c2[2], y/1920))
        d.line([(0,y),(1080,y)], fill=(r,g,b))

def _spark(d, n, fi, intens=1):
    random.seed(42)
    for _ in range(n):
        x, y = random.randint(50,1030), random.randint(100,1820)
        ph = random.random()*math.pi*2
        bl = (math.sin(fi*0.8+ph)+1)/2
        sz = int(2+bl*4*intens)
        c = random.choice([HPINK,GREEN,BLUE,LIME,LEMON,CORAL,PURPLE])
        a = bl*intens
        d.ellipse([x-sz,y-sz,x+sz,y+sz], fill=tuple(int(v*a) for v in c))

# ========== Scene 1: Hook ==========
def s_hook(fi, tot, p):
    img = _bg(); d = ImageDraw.Draw(img)
    _grad(d, (40,0,60), BG)
    _spark(d, 20, fi, 1.5)
    num_p = cl(p / 0.3)
    y_num = int(lr(2000, 450, eo(num_p)))
    jit = int(math.sin(fi*15)*12) if num_p < 0.9 else 0
    _center(d, y_num+jit, "73,000", ft(140), LIME)
    _center(d, y_num+180+jit, "次/秒", ft(72), GREEN)
    if p > 0.35:
        text_a = cl((p - 0.35) * 5)
        _center(d, 850, "你的Agent还在", ft(56), tuple(int(v*text_a) for v in GRAY))
        _center(d, 930, "用LLM管记忆？", ft(56), tuple(int(v*text_a) for v in CORAL))
    return img

# ========== Scene 2: 痛点 ==========
def s_pain(fi, tot, p):
    img = _bg(); d = ImageDraw.Draw(img)
    _grad(d, (30,0,0), BG)
    _spark(d, 12, fi, 0.6)
    items = [("向量检索", CORAL), ("LLM摘要", CORAL), ("图数据库", CORAL)]
    for i in range(3):
        trigger = i * 0.25
        item_p = cl((p - trigger) / 0.2)
        if item_p <= 0: continue
        y = 400 + i * 280
        x_off = int(lr(1200, 100, eo(item_p)))
        d.rounded_rectangle([x_off, y, x_off+880, y+220], radius=18, fill=(40,15,25))
        d.rounded_rectangle([x_off, y, x_off+880, y+220], radius=18, outline=items[i][1], width=3)
        d.text((x_off+40, y+60), items[i][0], fill=items[i][1], font=ft(64))
        d.text((x_off+750, y+60), "X", fill=items[i][1], font=ft(80))
    if p > 0.8:
        summary_a = cl((p - 0.8) * 5)
        _center(d, 1350, "全要大模型介入", ft(56), tuple(int(v*summary_a) for v in CORAL))
    return img

# ========== Scene 3: 转折+三层 ==========
def s_layers(fi, tot, p):
    img = _bg(); d = ImageDraw.Draw(img)
    _grad(d, (0,20,30), BG)
    _spark(d, 20, fi, 1.0)
    if p < 0.3:
        twist_p = cl(p / 0.3)
        sz = int(lr(20, 80, eo(twist_p)))
        _center(d, 600, "如果记忆能", ft(sz), tuple(int(v*cl(twist_p*2)) for v in GREEN))
        _center(d, 720, "自组织呢？", ft(sz+10), tuple(int(v*cl(twist_p*2)) for v in GREEN))
    else:
        _center(d, 350, "自组织", ft(72), GREEN)
        layers = [("1  锚点当晶种", GREEN), ("2  证据自动附着", BLUE), ("3  两笔就结晶", LEMON)]
        for i, (txt, col) in enumerate(layers):
            trigger = 0.3 + i * 0.22
            cp = cl((p - trigger) / 0.2)
            if cp <= 0: continue
            y = 500 + i * 280
            x = int(lr(1200, 80, eo(cp)))
            d.rounded_rectangle([x, y, x+920, y+240], radius=18, fill=(20,20,40))
            d.rounded_rectangle([x, y, x+920, y+240], radius=18, outline=col, width=3)
            d.text((x+40, y+50), txt, fill=col, font=ft(52))
    return img

# ========== Scene 4: 数据 ==========
def s_data(fi, tot, p):
    img = _bg(); d = ImageDraw.Draw(img)
    _grad(d, (5,25,15), BG)
    _spark(d, 25, fi)
    stats = [("73K/s", "写入速度", LIME), ("0.01ms", "单条延迟", BLUE), ("0 LLM", "零依赖", GREEN)]
    for i, (num, lab, col) in enumerate(stats):
        trigger = i * 0.25
        cp = cl((p - trigger) / 0.25)
        if cp <= 0: continue
        y = 350 + i * 320
        x = int(lr(1200, 80, eo(cp)))
        d.rounded_rectangle([x, y, x+920, y+260], radius=22, fill=(20,20,40))
        jit = int(math.sin(fi*12+i*5)*10) if cp < 0.8 else 0
        d.text((x+50+jit, y+20), num, fill=col, font=ft(100))
        d.text((x+50, y+160), lab, fill=GRAY, font=ft(36))
    if p > 0.7:
        _center(d, 1400, "pip install liquid-loop", ft(50), LEMON)
    return img

# ========== Scene 5: 开源截图展示 ==========
def s_open_source(fi, tot, p):
    """展示 GitHub 截图，从底部滑入 + 发光边框"""
    img = _bg(); d = ImageDraw.Draw(img)
    _grad(d, (15, 10, 35), BG)
    _spark(d, 30, fi, 1.2)

    # 标题：0-25% 弹入
    title_a = eo(cl(p / 0.25))
    if title_a > 0:
        _center(d, 180, "已开源", ft(80), tuple(int(v*title_a) for v in GREEN))
        _center(d, 300, "GitHub", ft(56), tuple(int(v*title_a) for v in WHITE))

    # GitHub 截图：20% 开始从底部滑入
    ss_p = cl((p - 0.20) / 0.35)
    if ss_p > 0:
        try:
            ss = Image.open(GITHUB_SCREENSHOT).convert("RGB")
            # 截图缩放：宽度 900px，高度按比例
            target_w = 900
            ratio = target_w / ss.width
            target_h = int(ss.height * ratio)
            ss = ss.resize((target_w, target_h), Image.LANCZOS)

            # 滑入位置
            ss_x = (1080 - target_w) // 2
            ss_y_start = 1920
            ss_y_end = (1920 - target_h) // 2 + 80
            ss_y = int(lr(ss_y_start, ss_y_end, eo(ss_p)))

            # 发光边框
            glow_a = cl((p - 0.4) * 3)
            if glow_a > 0:
                glow_color = tuple(int(v * glow_a * 0.6) for v in GREEN)
                d.rectangle([ss_x-4, ss_y-4, ss_x+target_w+4, ss_y+target_h+4], outline=glow_color, width=3)

            img.paste(ss, (ss_x, ss_y))
        except Exception as e:
            _center(d, 800, f"[截图加载失败]", ft(40), CORAL)

    # 底部文字：60% 出现
    if p > 0.6:
        bot_a = cl((p - 0.6) * 3)
        _center(d, 1550, "MIT License · 零依赖", ft(44), tuple(int(v*bot_a) for v in LIME))
        _center(d, 1620, "pip install liquid-loop", ft(40), tuple(int(v*bot_a) for v in LEMON))

    return img

# ========== Scene 6: CTA ==========
def s_cta(fi, tot, p):
    img = _bg(); d = ImageDraw.Draw(img)
    _grad(d, (25,0,40), BG)
    _spark(d, 30, fi, 1.8)
    ga = eo(cl(p / 0.25))
    if ga > 0:
        d.rounded_rectangle([100,400,980,580], radius=20, fill=(30,15,50))
        d.text((160,420), "GitHub", fill=CORAL, font=ft(64))
        d.text((160,500), "fishbook0001/liquid-loop", fill=WHITE, font=ft(36))
    ca = eo(cl((p-0.35)/0.3))
    if ca > 0:
        pulse = 0.6+0.4*math.sin(p*25)
        _center(d, 750, "关注", ft(90), tuple(int(v*ca*pulse) for v in HPINK))
        _center(d, 900, "更多 Agent 架构干货", ft(48), GREEN)
    return img

# ---- TTS ----
TTS_LINES = [
    ("七万三千次写入。你的Agent还在用大模型管记忆？", "hook"),
    ("向量检索、LLM摘要、图数据库，全要大模型介入。", "pain"),
    ("如果记忆能自组织呢？锚点当晶种，两笔就结晶。", "layers"),
    ("零LLM依赖，pip一行搞定。", "data"),
    ("已经开源了，MIT协议。", "open_source"),
    ("搜液环，关注我。", "cta"),
]

SCENE_FN = {"hook": s_hook, "pain": s_pain, "layers": s_layers, "data": s_data, "open_source": s_open_source, "cta": s_cta}
SCENE_ORDER = ["hook", "pain", "layers", "data", "open_source", "cta"]

def gen_tts():
    print("TTS...")
    script = os.path.join(AUDIO, "gen_v9.py")
    lines_code = json.dumps(TTS_LINES, ensure_ascii=False)
    with open(script, "w") as f:
        f.write(f'import asyncio, edge_tts, os\nLINES = {lines_code}\n')
        f.write('async def main():\n')
        f.write('    for i, (text, name) in enumerate(LINES):\n')
        f.write(f'        out = os.path.join("{AUDIO}", f"v9_{{name}}.mp3")\n')
        f.write('        comm = edge_tts.Communicate(text, "zh-CN-YunxiNeural", rate="+15%")\n')
        f.write('        await comm.save(out)\n')
        f.write('        print(f"  OK {name}")\n')
        f.write('asyncio.run(main())\n')
    subprocess.run([TP, script], check=True)

def get_dur(p):
    r = subprocess.run([FP, "-v","quiet","-show_entries","format=duration","-of","csv=p=0",p],
                       capture_output=True, text=True)
    return float(r.stdout.strip())

def build_timeline():
    tl = []; t = 0.0
    for name in SCENE_ORDER:
        ap = os.path.join(AUDIO, f"v9_{name}.mp3")
        ad = get_dur(ap)
        scene_end = t + ad + 0.005
        tl.append({"name": name, "start": t, "end": scene_end, "audio_dur": ad, "fn": SCENE_FN[name]})
        t = scene_end
    total = t
    print(f"  总时长: {total:.1f}s")
    for s in tl:
        print(f"    {s['name']}: {s['start']:.1f}-{s['end']:.1f} (audio {s['audio_dur']:.1f}s)")
    return tl, total

def render_frames(tl, total):
    n = int(total * FPS)
    print(f"Rendering {n} frames...")
    for fi in range(n):
        s = fi / FPS
        for seg in tl:
            if seg["start"] <= s < seg["end"]:
                lf = fi - int(seg["start"] * FPS)
                lt = int((seg["end"] - seg["start"]) * FPS)
                p = cl(lf / lt) if lt > 0 else 0
                img = seg["fn"](fi, n, p)
                # 缩放动效
                zoom_p = cl(p / 0.2)
                z = 1 + 0.02 * eo(zoom_p)
                cw, ch = int(1080*z), int(1920*z)
                img = img.resize((cw, ch), Image.LANCZOS)
                x0, y0 = (cw-1080)//2, (ch-1920)//2
                img = img.crop((x0, y0, x0+1080, y0+1920))
                img.save(os.path.join(FRAMES, f"f_{fi:05d}.png"))
                break
        if fi % (FPS*2) == 0:
            print(f"  {fi}/{n} ({fi*100//n}%)")
    print(f"  Done {n} frames")

def make_audio(tl, total):
    print("Mixing audio...")
    inputs = []; filters = []
    for i, seg in enumerate(tl):
        ap = os.path.join(AUDIO, f"v9_{seg['name']}.mp3")
        inputs += ["-i", ap]
        delay_ms = int(seg["start"] * 1000)
        filters.append(f"[{i}]adelay={delay_ms}|{delay_ms}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(tl)))
    filters.append(f"{mix}amix=inputs={len(tl)}:duration=longest:dropout_transition=0[voice]")
    vw = os.path.join(AUDIO, "v9_voice.wav")
    subprocess.run([FF, "-y"] + inputs + ["-filter_complex", ";".join(filters),
                   "-map", "[voice]", "-ar", "44100", "-ac", "1", vw],
                   check=True, capture_output=True)
    vd = get_dur(vw)
    print(f"  Voice: {vd:.1f}s")
    final = os.path.join(AUDIO, "v9_final.wav")
    subprocess.run([FF,"-y","-i",vw,"-af","volume=1.5","-ar","44100",final], check=True, capture_output=True)
    fd = get_dur(final)
    print(f"  Final: {fd:.1f}s")
    return final, fd

def encode(final_audio, audio_dur):
    print("Encoding...")
    out = os.path.join(OUTPUT, "liquid-loop-douyin-v9.mp4")
    target = audio_dur + 2.0
    subprocess.run([FF,"-y","-framerate",str(FPS),
        "-i",os.path.join(FRAMES,"f_%05d.png"),"-i",final_audio,
        "-c:v","libx264","-b:v","5M","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","192k",
        "-t",str(target),
        "-movflags","+faststart",out], check=True)
    r = subprocess.run([FP,"-v","quiet","-show_entries","format=duration,size","-of","json",out],
                       capture_output=True, text=True)
    info = json.loads(r.stdout)
    dur = float(info['format']['duration'])
    size_mb = int(info['format']['size'])/1024/1024
    print(f"  {out} | {dur:.1f}s | {size_mb:.1f}MB")
    if dur < audio_dur:
        print(f"  WARNING: truncation!")
    else:
        print(f"  OK: no truncation")
    return out

if __name__ == "__main__":
    print("="*50)
    print("  液环抖音 v9 · 开源截图 · 20s+ · 多巴胺 · 纯解说")
    print("="*50)
    gen_tts()
    tl, total = build_timeline()
    render_frames(tl, total)
    final_audio, audio_dur = make_audio(tl, total)
    encode(final_audio, audio_dur)
    print("\nDone!")
