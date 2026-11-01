"""Run with:  python tests/test_poster.py — the poster module: which image, the SSRF guard, and a REAL blur (needs Pillow)."""
import asyncio, io
from unittest import mock

import _stubs
import poster

def png(size=(640, 360)):
    from PIL import Image
    im = Image.new("RGB", size, (255, 255, 255))
    for x in range(0, size[0], 10):
        for y in range(0, size[1], 10):
            if (x // 10 + y // 10) % 2: im.paste((0, 0, 0), (x, y, x + 10, y + 10))
    out = io.BytesIO(); im.save(out, "PNG"); return out.getvalue()

def edge_energy(im):                                  # mean |difference| between neighbouring pixels = how much detail there is
    g = im.convert("L"); w, h = g.size; px = g.tobytes()
    return sum(abs(px[y * w + x] - px[y * w + x + 1]) for y in range(h) for x in range(w - 1)) / (w * h)

async def t_modes_and_picking():
    assert [poster.next_mode(m) for m in ("off", "spoiler", "blur", "zzz")] == ["spoiler", "blur", "off", "off"]
    assert await poster.prepare("", "off") is None and await poster.prepare("ftp://x/y.jpg", "blur") is None
    assert (await poster.prepare("https://i/a.jpg", "off")).spoiler is False and (await poster.prepare("https://i/a.jpg", "spoiler")).spoiler is True

async def t_blur_really_removes_detail_and_only_the_blurred_copy_is_uploaded():
    from PIL import Image
    raw = png()
    async def fetch(url): return raw
    poster._cache.clear()
    with mock.patch.object(poster, "_fetch", fetch):
        p = await poster.prepare("https://i/a.jpg", "blur")
    media = p.media()
    assert p.data and p.spoiler is False and media.name == "poster.jpg" and not isinstance(media, str), "a fresh in-memory file, not the original URL"
    data = media.read()
    assert data[:2] == b"\xff\xd8" and edge_energy(Image.open(io.BytesIO(data))) < 0.15 * edge_energy(Image.open(io.BytesIO(raw)))
    with mock.patch.object(poster, "_fetch", None):                      # served from the cache: one blur serves every channel
        assert (await poster.prepare("https://i/a.jpg", "blur")).data == p.data

async def t_a_blur_that_cannot_be_made_never_shows_the_sharp_image():
    async def broken(url): raise ValueError("download failed")
    poster._cache.clear()
    with mock.patch.object(poster, "_fetch", broken):
        p = await poster.prepare("https://i/a.jpg", "blur")
    assert p.spoiler is True and p.data is None
    async def not_an_image(url): return b"<html>not an image</html>"
    poster._cache.clear()
    with mock.patch.object(poster, "_fetch", not_an_image):
        assert (await poster.prepare("https://i/a.jpg", "blur")).spoiler is True

async def t_poster_downloads_cannot_reach_private_addresses():
    for url in ("http://127.0.0.1/a.jpg", "http://10.0.0.5/a.jpg", "http://192.168.1.1/a.jpg", "http://[::1]/a.jpg", "http://169.254.169.254/latest"):
        assert not await poster._public_host(url), url
    assert await poster._public_host("http://8.8.8.8/a.jpg")

async def main():
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            await fn()
            print("ok ", name[2:].replace("_", " "))
    print("all poster checks passed")

asyncio.run(main())
