"""Sistem içi tasarım stüdyosu: görsel ve video üretimi dış servis (Virale) kullanmadan.

Görsel: ajan tasarımı SVG olarak yazar (zemin, gradyan, şekiller, tipografi, ürün dekupesi, yüklenen
fotoğraflar); panel CairoSVG ile PNG'ye çevirir. Video: ajanın tasarladığı kareler ffmpeg ile yakınlaşma,
kaydırma, geçiş, yazı katmanı ve müzikle 9:16 / 4:5 / 1:1 MP4'e kurgulanır. Tek maliyet ajanın Claude
kullanımıdır.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from . import storage

SIZES = {"feed": (1080, 1350), "square": (1080, 1080), "story": (1080, 1920)}
FONTS = "Inter (300–900 kalınlık), DejaVu Sans, DejaVu Serif"
REF_RE = re.compile(r'''((?:xlink:)?href)\s*=\s*(["'])(media|uploads):([^"']+)\2''')
MAX_SVG = 400_000
MAX_EMBED_SIDE = 1600
MOTIONS = ("still", "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down")
TRANSITIONS = ("fade", "dissolve", "slideleft", "slideup", "circleopen", "wipeleft", "none")
FPS = 30


# ---------------------------------------------------------------- görsel (SVG → PNG)


def _embed(area: str, name: str) -> str:
    """Kütüphane veya yüklenen görseli SVG'ye gömülecek data URI yapar (büyükse küçültülür)."""
    path = storage.area_file(area, name.strip())
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((MAX_EMBED_SIDE, MAX_EMBED_SIDE))
        alpha = img.mode in ("RGBA", "LA", "P")
        img = img.convert("RGBA" if alpha else "RGB")
        buf = io.BytesIO()
        img.save(buf, "PNG" if alpha else "JPEG", quality=90)
    mime = "image/png" if alpha else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


def _only_data_urls(url: str, resource_type: str = "") -> bytes:
    # Ajanın yazdığı SVG dışarıya (http, dosya sistemi) erişemesin; yalnız gömülü görseller açılır.
    from cairosvg.url import fetch

    if not url.startswith("data:"):
        raise ValueError("SVG içinde yalnız media:/uploads: görselleri kullanılabilir")
    return fetch(url, resource_type)


def render_svg(svg: str, fmt: str) -> Image.Image:
    if fmt not in SIZES:
        raise ValueError(f"format şunlardan biri olmalı: {', '.join(SIZES)}")
    if len(svg) > MAX_SVG:
        raise ValueError("SVG çok büyük; görselleri media:/uploads: referansıyla ver, base64 gömme")
    if re.search(r"<!ENTITY|<script|<foreignObject", svg, re.I):
        raise ValueError("SVG'de script, foreignObject veya ENTITY kullanılamaz")
    svg = REF_RE.sub(lambda m: f'{m.group(1)}={m.group(2)}{_embed(m.group(3), m.group(4))}{m.group(2)}', svg)
    w, h = SIZES[fmt]
    from cairosvg.surface import PNGSurface

    png = PNGSurface.convert(bytestring=svg.encode("utf-8"), output_width=w, output_height=h,
                             url_fetcher=_only_data_urls, unsafe=False)
    img = Image.open(io.BytesIO(png))
    img.load()
    return img


def design_to_media(svg: str, fmt: str, filename: str, transparent: bool) -> dict[str, Any]:
    img = render_svg(svg, fmt)
    stem = storage.slug(Path(filename).stem) or "tasarim"
    if transparent:
        target = storage.unique_path("media", f"{stem}.png")
        img.convert("RGBA").save(target, "PNG", optimize=True)
    else:
        flat = Image.new("RGB", img.size, "white")
        flat.paste(img.convert("RGBA"), mask=img.convert("RGBA").getchannel("A"))
        img = flat
        target = storage.unique_path("media", f"{stem}.jpg")
        img.save(target, "JPEG", quality=92, optimize=True)
    prev = img.convert("RGB")
    prev.thumbnail((720, 720))
    buf = io.BytesIO()
    prev.save(buf, "JPEG", quality=82)
    return {"name": target.name, "size": list(img.size), "preview": buf.getvalue()}


# ---------------------------------------------------------------- video (ffmpeg)


def _frame(area_name: str, size: tuple[int, int], workdir: Path, idx: str) -> Path:
    """Sahne görselini kadraja kırpar (cover), şeffaf alanı beyazla doldurur, hareket payı için 2 kat büyütür."""
    area, _, name = area_name.partition(":") if ":" in area_name else ("media", "", area_name)
    path = storage.area_file(area, name.strip())
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGBA")
        flat = Image.new("RGB", img.size, "white")
        flat.paste(img, mask=img.getchannel("A"))
        big = (size[0] * 2, size[1] * 2)
        if abs(flat.width / flat.height - size[0] / size[1]) < 0.05:
            out = ImageOps.fit(flat, big, Image.LANCZOS)
        else:
            # oranı farklı kare (ör. 4:5 tasarım, 9:16 video): yazılar kesilmesin, bulanık zemin üzerinde tamamı görünsün
            from PIL import ImageFilter

            out = ImageOps.fit(flat, (big[0] // 8, big[1] // 8)).filter(ImageFilter.GaussianBlur(6)).resize(big, Image.BILINEAR)
            fg = ImageOps.contain(flat, big, Image.LANCZOS)
            out.paste(fg, ((big[0] - fg.width) // 2, (big[1] - fg.height) // 2))
    target = workdir / f"s{idx}.png"
    out.save(target, "PNG")
    return target


def _overlay(area_name: str, size: tuple[int, int], workdir: Path, idx: str) -> Path:
    area, _, name = area_name.partition(":") if ":" in area_name else ("media", "", area_name)
    path = storage.area_file(area, name.strip())
    with Image.open(path) as img:
        out = ImageOps.exif_transpose(img).convert("RGBA").resize(size, Image.LANCZOS)
    target = workdir / f"o{idx}.png"
    out.save(target, "PNG")
    return target


def _motion(kind: str, frames: int, w: int, h: int) -> str:
    n = max(frames - 1, 1)
    center = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    expr = {
        "still": f"z=1:{center}",
        "zoom_in": f"z='1+0.12*on/{n}':{center}",
        "zoom_out": f"z='1.12-0.12*on/{n}':{center}",
        "pan_left": f"z=1.12:x='(iw-iw/zoom)*(1-on/{n})':y='ih/2-(ih/zoom/2)'",
        "pan_right": f"z=1.12:x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'",
        "pan_up": f"z=1.12:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-on/{n})'",
        "pan_down": f"z=1.12:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*on/{n}'",
    }[kind]
    return f"zoompan={expr}:d={frames}:s={w}x{h}:fps={FPS}"


async def make_video(scenes: list[dict[str, Any]], fmt: str, transition: str, audio: str, filename: str) -> dict[str, Any]:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Sunucuda ffmpeg yok; video üretilemiyor")
    if fmt not in SIZES:
        raise ValueError(f"format şunlardan biri olmalı: {', '.join(SIZES)}")
    if not 1 <= len(scenes) <= 12:
        raise ValueError("Video 1–12 sahneden oluşmalı")
    if transition not in TRANSITIONS:
        raise ValueError(f"transition şunlardan biri olmalı: {', '.join(TRANSITIONS)}")
    w, h = SIZES[fmt]
    t = 0.0 if transition == "none" or len(scenes) == 1 else 0.5
    durations = [max(1.2, min(float(s.get("seconds") or 3), 10.0)) for s in scenes]
    if sum(durations) - t * (len(scenes) - 1) > 60:
        raise ValueError("Video en fazla 60 saniye olabilir")
    for s in scenes:
        if (s.get("motion") or "still") not in MOTIONS:
            raise ValueError(f"motion şunlardan biri olmalı: {', '.join(MOTIONS)}")

    (storage.DATA_DIR / "cache").mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="video-", dir=storage.DATA_DIR / "cache"))
    try:
        inputs: list[str] = []
        filters: list[str] = []
        idx = 0
        labels = []
        for i, (scene, dur) in enumerate(zip(scenes, durations)):
            frames = int(round(dur * FPS))
            img = await asyncio.to_thread(_frame, scene["image"], (w, h), workdir, str(i))
            inputs += ["-i", str(img)]
            base = idx
            idx += 1
            chain = f"[{base}:v]{_motion(scene.get('motion') or 'still', frames, w, h)},format=yuv420p,setsar=1[v{i}]"
            if scene.get("overlay"):
                ov = await asyncio.to_thread(_overlay, scene["overlay"], (w, h), workdir, str(i))
                inputs += ["-loop", "1", "-t", f"{dur:.2f}", "-i", str(ov)]
                delay = max(0.0, min(float(scene.get("overlay_delay") or 0.4), dur - 0.5))
                chain = (f"[{base}:v]{_motion(scene.get('motion') or 'still', frames, w, h)},setsar=1[b{i}];"
                         f"[{idx}:v]format=rgba,fade=in:st={delay:.2f}:d=0.5:alpha=1[o{i}];"
                         f"[b{i}][o{i}]overlay=0:0:shortest=1,format=yuv420p[v{i}]")
                idx += 1
            filters.append(chain)
            labels.append(f"v{i}")
        last, length = labels[0], durations[0]
        for i in range(1, len(labels)):
            out = f"x{i}"
            if t:
                filters.append(f"[{last}][{labels[i]}]xfade=transition={transition}:duration={t}:offset={length - t:.3f}[{out}]")
                length += durations[i] - t
            else:
                filters.append(f"[{last}][{labels[i]}]concat=n=2:v=1:a=0[{out}]")
                length += durations[i]
            last = out
        audio_args: list[str] = []
        if audio:
            area, _, name = audio.partition(":") if ":" in audio else ("uploads", "", audio)
            apath = storage.area_file(area, name.strip())
            inputs += ["-i", str(apath)]
            filters.append(f"[{idx}:a]atrim=0:{length:.2f},afade=t=out:st={max(length - 1.0, 0):.2f}:d=1[a]")
            audio_args = ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
        stem = storage.slug(Path(filename).stem) or "video"
        target = storage.unique_path("media", f"{stem}.mp4")
        tmp = workdir / "out.mp4"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
               "-map", f"[{last}]", *audio_args, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
               "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart", "-t", f"{length:.2f}", str(tmp)]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=420)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("Video kurgusu 7 dakikada bitmedi; sahne sayısını veya süreyi azalt")
        if proc.returncode != 0 or not tmp.is_file():
            raise RuntimeError("ffmpeg hatası: " + err.decode("utf-8", "replace")[-600:])
        shutil.move(str(tmp), target)
        return {"name": target.name, "seconds": round(length, 1), "size": [w, h]}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def contact_sheet(video: Path, count: int = 4) -> bytes | None:
    """Videodan eşit aralıklı birkaç kareyi yan yana koyar; ajan sonucu görüp kontrol eder."""
    import subprocess

    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf",
                              f"fps=1/1.6,scale=270:-2,tile={count}x1", "-frames:v", "1", "-f", "image2pipe",
                              "-vcodec", "mjpeg", "-"], capture_output=True, timeout=60, check=True)
        return out.stdout or None
    except (subprocess.SubprocessError, OSError):
        return None
