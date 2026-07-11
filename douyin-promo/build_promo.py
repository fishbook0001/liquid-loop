#!/usr/bin/env python3
"""液环抖音引流视频生成器 — 9:16竖屏 (1080x1920)"""
import os, subprocess, math, json
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FPS = 30
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
FRAME_DIR = os.path.join(OUT_DIR, "frames")
AUDIO_DIR = os.path.join(OUT_DIR, "audio")
os.makedirs(FRAME_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

# ── 颜色 ──────────────────────────────────────────────────
BG_DARK   = (15, 15, 25)
BG_CARD   = (30, 30, 50)
ACCENT    = (0, 200, 150)     # 青绿色
ACCENT2   = (100, 180, 255)   # 蓝色
WHITE     = (255, 255, 255)
GRAY      = (150, 150, 170)
RED_SOFT  = (255, 90, 90)
ORANGE    = (255, 180, 50)
GREEN     = (50, 220, 120)

# ── 字体 ──────────────────────────────────────────────────
FONT_PATHS = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]
def get_font(size):
    for fp in FONT_PATHS:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except:
                continue
    return ImageFont.load_default()

FONT_BIG    = get_font(72)
FONT_MID    = get_font(52)
FONT_SMALL  = get_font(38)
FONT_TINY   = get_font(30)
FONT_CODE   = get_font(34)

# ── 工具函数 ──────────────────────────────────────────────
def new_frame():
    return Image.new("RGB", (W, H), BG_DARK)

def draw_centered(draw, y, text, font, color=WHITE):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, y), text, fill=color, font=font)

def ease_out(t):
    return 1 - (1 - t) ** 3

def ease_in_out(t):
    return 3 * t * t - 2 * t * t * t

def lerp(a, b, t):
    return a + (b - a) * t

# ── 场景定义 ──────────────────────────────────────────────
# 每个场景: (开始秒, 结束秒, 绘制函数)
# 绘制函数: (frame_index, total_frames) -> Image

def scene_hook(fi, total):
    """Scene 1: Hook — 文字从底部弹入"""
    img = new_frame()
    draw = ImageDraw.Draw(img)
    t = min(fi / total, 1.0)
    y_offset = int(lerp(200, 0, ease_out(t)))
    alpha = min(fi / (total * 0.3), 1.0)
    color = tuple(int(c * alpha) for c in WHITE)
    draw_centered(draw, 800 + y_offset, "你的 Agent", FONT_BIG, color)
    draw_centered(draw, 900 + y_offset, "还在用 LLM", FONT_BIG, color)
    draw_centered(draw, 1000 + y_offset, "管记忆？", FONT_BIG, ACCENT)
    return img

def scene_pain(fi, total):
    """Scene 2: 痛点卡片"""
    img = new_frame()
    draw = ImageDraw.Draw(img)
    cards = [
        ("向量检索", "需要外部评分排序", RED_SOFT),
        ("LLM 摘要", "需要外部提取压缩", ORANGE),
        ("图数据库", "需要 LLM 诊断器", RED_SOFT),
    ]
    for i, (title, desc, color) in enumerate(cards):
        delay = i * 0.25
        t = max(0, min((fi / total - delay) / 0.3, 1.0))
        if t <= 0:
            continue
        x = int(lerp(W + 100, 80, ease_out(t)))
        y = 650 + i * 280
        # card bg
        draw.rounded_rectangle([x, y, x + 900, y + 220], radius=20, fill=BG_CARD)
        # left accent bar
        draw.rectangle([x, y, x + 8, y + 220], fill=color)
        # text
        draw.text((x + 40, y + 30), title, fill=color, font=FONT_MID)
        draw.text((x + 40, y + 110), desc, fill=GRAY, font=FONT_SMALL)
    # bottom text
    if fi / total > 0.8:
        draw_centered(draw, 1600, "全都要大模型介入", FONT_MID, WHITE)
    return img

def scene_twist(fi, total):
    """Scene 3: 转折"""
    img = new_frame()
    draw = ImageDraw.Draw(img)
    t = min(fi / total, 1.0)
    # 背景渐变
    r = int(lerp(15, 25, ease_in_out(t)))
    g = int(lerp(15, 35, ease_in_out(t)))
    b = int(lerp(25, 60, ease_in_out(t)))
    draw.rectangle([0, 0, W, H], fill=(r, g, b))
    # 文字缩放
    scale = ease_out(t)
    sz = int(72 * (0.8 + 0.2 * scale))
    font = get_font(sz)
    alpha = min(fi / (total * 0.2), 1.0)
    color = tuple(int(c * alpha) for c in ACCENT)
    draw_centered(draw, 820, "如果记忆能", font, color)
    draw_centered(draw, 920, "自组织呢？", font, color)
    if t > 0.5:
        sub_alpha = min((t - 0.5) * 4, 1.0)
        sub_color = tuple(int(c * sub_alpha) for c in WHITE)
        draw_centered(draw, 1080, "不需要任何外部干预", FONT_MID, sub_color)
    return img

def scene_principle(fi, total):
    """Scene 4: 核心原理 — 三层结构"""
    img = new_frame()
    draw = ImageDraw.Draw(img)
    t = fi / total
    # 标题
    draw_centered(draw, 200, "液环原理", FONT_BIG, ACCENT)
    # 三层
    layers = [
        ("🔹 锚点 — 晶种", "认知关注点，有稳定性值", 500),
        ("🔸 证据 — 附着粒子", "具体观察，权重指数衰减", 750),
        ("💎 结晶 — 自动生成", "2+ 一致证据 → 记忆", 1000),
    ]
    for i, (title, desc, base_y) in enumerate(layers):
        delay = i * 0.2
        lt = max(0, min((t - delay) / 0.25, 1.0))
        if lt <= 0:
            continue
        # 渐入
        a = ease_out(lt)
        y = int(lerp(base_y + 60, base_y, a))
        color = tuple(int(c * a) for c in WHITE)
        # card
        draw.rounded_rectangle([100, y, 980, y + 180], radius=16, fill=BG_CARD)
        draw.text((140, y + 20), title, fill=ACCENT if i == 2 else ACCENT2, font=FONT_MID)
        draw.text((140, y + 90), desc, fill=GRAY, font=FONT_SMALL)
    # 箭头连接
    if t > 0.5:
        arrow_a = min((t - 0.5) * 4, 1.0)
        arrow_color = tuple(int(c * arrow_a) for c in ACCENT)
        for y_arrow in [690, 940]:
            draw.polygon([(540, y_arrow), (520, y_arrow - 15), (560, y_arrow - 15)], fill=arrow_color)
    # 底部说明
    if t > 0.6:
        ba = min((t - 0.6) * 3, 1.0)
        bc = tuple(int(c * ba) for c in WHITE)
        draw_centered(draw, 1250, "熵值天然反映认知健康度", FONT_MID, bc)
    return img

def scene_perf(fi, total):
    """Scene 5: 性能数据"""
    img = new_frame()
    draw = ImageDraw.Draw(img)
    t = fi / total
    draw_centered(draw, 300, "性能实测", FONT_BIG, ACCENT)
    stats = [
        ("73K", "写入/秒", GREEN),
        ("0.014ms", "单条延迟", ACCENT2),
        ("0", "LLM 依赖", ACCENT),
    ]
    for i, (num, label, color) in enumerate(stats):
        delay = i * 0.2
        lt = max(0, min((t - delay) / 0.25, 1.0))
        if lt <= 0:
            continue
        a = ease_out(lt)
        y = int(lerp(600 + i * 300 + 80, 600 + i * 300, a))
        draw.rounded_rectangle([120, y, 960, y + 220], radius=20, fill=BG_CARD)
        draw.text((200, y + 30), num, fill=color, font=get_font(80))
        draw.text((200, y + 140), label, fill=GRAY, font=FONT_SMALL)
    # bottom
    if t > 0.7:
        ba = min((t - 0.7) * 4, 1.0)
        bc = tuple(int(c * ba) for c in WHITE)
        draw_centered(draw, 1580, "纯 Python · pip install 一行搞定", FONT_SMALL, bc)
    return img

def scene_code(fi, total):
    """Scene 6: 代码展示 — 打字机效果"""
    img = new_frame()
    draw = ImageDraw.Draw(img)
    t = fi / total
    draw_centered(draw, 200, "五行代码", FONT_BIG, ACCENT)
    lines = [
        "from liquid_loop import WorkspaceState",
        "",
        "state = WorkspaceState()",
        'state.add_anchor("核心目标")',
        'state.add_evidence("核心目标",',
        '    "用户要简洁输出")',
        "",
        "# ✓ 自动生成结晶记忆",
    ]
    chars_shown = int(t * 80)  # 总字符数
    count = 0
    for i, line in enumerate(lines):
        if count >= chars_shown:
            break
        visible = line[:max(0, chars_shown - count)]
        count += len(line)
        y = 400 + i * 65
        color = GREEN if "# " in line else (ACCENT2 if "import" in line or "WorkspaceState" in line else WHITE)
        if visible:
            draw.text((120, y), visible, fill=color, font=FONT_CODE)
    # cursor blink
    if int(fi / 4) % 2 == 0 and chars_shown < 80:
        # find cursor position
        c = 0
        for i, line in enumerate(lines):
            if c + len(line) >= chars_shown:
                partial = line[:chars_shown - c]
                bbox = draw.textbbox((120, 400 + i * 65), partial, font=FONT_CODE)
                draw.rectangle([bbox[2] + 2, 400 + i * 65, bbox[2] + 20, 400 + i * 65 + 50], fill=ACCENT)
                break
            c += len(line)
    return img

def scene_cta(fi, total):
    """Scene 7: CTA"""
    img = new_frame()
    draw = ImageDraw.Draw(img)
    t = fi / total
    # GitHub
    ga = ease_out(min(t / 0.3, 1.0))
    draw.rounded_rectangle([100, 500, 980, 680], radius=20, fill=(30, 30, 50))
    draw.text((160, 520), "⭐ GitHub", fill=ORANGE, font=FONT_MID)
    draw.text((160, 590), "fishbook0001/liquid-loop", fill=WHITE, font=FONT_SMALL)
    # PyPI
    pa = ease_out(min(max(t - 0.15, 0) / 0.3, 1.0))
    if pa > 0:
        draw.rounded_rectangle([100, 740, 980, 920], radius=20, fill=(30, 30, 50))
        draw.text((160, 760), "📦 PyPI", fill=GREEN, font=FONT_MID)
        draw.text((160, 830), "pip install liquid-loop", fill=WHITE, font=FONT_SMALL)
    # CTA text
    ca = ease_out(min(max(t - 0.4, 0) / 0.3, 1.0))
    if ca > 0:
        cc = tuple(int(c * ca) for c in WHITE)
        draw_centered(draw, 1100, "关注获取更多", FONT_MID, cc)
        draw_centered(draw, 1180, "Agent 架构干货", FONT_MID, ACCENT)
    # pulse effect on star
    if t > 0.5:
        pulse = 0.5 + 0.5 * math.sin((t - 0.5) * 20)
        star_color = tuple(int(lerp(200, 255, pulse)) for _ in range(3))
        draw.text((820, 525), "★", fill=star_color, font=get_font(50))
    return img

# ── 场景时间轴 ────────────────────────────────────────────
SCENES = [
    (0,   4,   scene_hook),      # 0-4s
    (4,  11,   scene_pain),      # 4-11s
    (11, 17,   scene_twist),     # 11-17s
    (17, 34,   scene_principle), # 17-34s
    (34, 44,   scene_perf),      # 34-44s
    (44, 52,   scene_code),      # 44-52s
    (52, 60,   scene_cta),       # 52-60s
]

# ── 配音文案（用于 edge-tts）──────────────────────────────
TTS_TEXT = """你的Agent还在用大模型管理记忆？
向量检索要外部评分，LLM摘要要外部压缩，图数据库要LLLM诊断。全都要大模型介入。
如果记忆能自组织呢？不需要任何外部干预。
液环的原理很简单。注入证据，证据自动聚向锚点。两条以上一致的证据，自动结晶成记忆。不需要排序，不需要检索。熵值天然反映认知健康度。
每秒七万三千次写入，单条延迟零点零一四毫秒。零LLM依赖，纯Python。
五行代码，自组织记忆。
GitHub搜液环，star一下。关注我，后面还有更多Agent架构干货。"""

def generate_frames():
    print("🎬 生成帧...")
    total_seconds = SCENES[-1][1]  # 最后场景结束秒数
    total_frames = total_seconds * FPS
    for fi in range(total_frames):
        sec = fi / FPS
        # 找当前场景
        for start, end, renderer in SCENES:
            if start <= sec < end:
                local_fi = fi - start * FPS
                local_total = (end - start) * FPS
                img = renderer(local_fi, local_total)
                img.save(os.path.join(FRAME_DIR, f"frame_{fi:05d}.png"))
                break
        if fi % (FPS * 5) == 0:
            print(f"  frame {fi}/{total_frames} ({fi*100//total_frames}%)")
    print(f"  ✅ 共 {total_frames} 帧")

def generate_tts():
    print("🎤 生成配音...")
    tts_script = os.path.join(OUT_DIR, "tts_gen.py")
    with open(tts_script, "w") as f:
        f.write(f'''import asyncio, edge_tts
TEXT = """{TTS_TEXT}"""
async def main():
    comm = edge_tts.Communicate(TEXT, "zh-CN-YunxiNeural", rate="+10%")
    await comm.save("{os.path.join(AUDIO_DIR, 'voice.mp3')}")
    print("✅ 配音完成")
asyncio.run(main())
''')
    subprocess.run([os.path.expanduser("~/.qwenpaw/venv/bin/python3"), tts_script], check=True)

def compose_video():
    print("🎞️  合成视频...")
    audio_path = os.path.join(AUDIO_DIR, "voice.mp3")
    output_path = os.path.join(OUT_DIR, "liquid-loop-douyin.mp4")
    
    # 获取音频时长
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True
    )
    audio_dur = float(probe.stdout.strip())
    print(f"  音频时长: {audio_dur:.1f}s")
    
    # 用配音时长驱动视频（而不是硬编码帧数）
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-i", os.path.join(FRAME_DIR, "frame_%05d.png"),
        "-i", audio_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-t", str(audio_dur + 0.5),  # 留 0.5s 余量
        output_path
    ]
    subprocess.run(cmd, check=True)
    
    # 查看输出
    probe2 = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries",
         "stream=width,height,duration,codec_name",
         "-of", "json", output_path],
        capture_output=True, text=True
    )
    info = json.loads(probe2.stdout)
    print(f"  ✅ 输出: {output_path}")
    for s in info.get("streams", []):
        if "width" in s:
            print(f"     {s['width']}x{s['height']} {s.get('codec_name','')}")
        if "duration" in s:
            print(f"     时长: {float(s['duration']):.1f}s")
    return output_path

if __name__ == "__main__":
    generate_frames()
    generate_tts()
    out = compose_video()
    print(f"\n🎉 完成！视频: {out}")
