/*
 * Halftone — renders any image (or a procedural scene) as an ink-dot print.
 *
 * Markup:
 *   <figure class="ht" data-src="assets/portrait.png" data-alt="…"></figure>
 *   <figure class="ht" data-scene="ridges" data-alt="…"></figure>
 *
 * Options (data-* attributes, all optional):
 *   data-cell      dot pitch in CSS px (default 7, 5 on small screens)
 *   data-contrast  >1 punches midtones (default 1.15)
 *   data-bright    lightens (+) or darkens (-) mid-tones, -1..1; white stays paper (default 0)
 *   data-inset     trims this fraction off every source edge, e.g. 0.02 (default 0)
 *   data-white     source lightness treated as bare paper (default 0.9)
 *   data-black     source lightness treated as solid ink (default 0.04)
 *   data-sharpen   edge emphasis before dotting (default 0.6)
 *   data-fade      "top" | "bottom" | "none" — dissolve one edge into paper
 *   data-focus     "x y" (0..1) crop focus for cover fit (default "0.5 0.5")
 *   data-fit       "cover" | "contain" (default cover)
 *   data-interactive  dots part around the pointer
 */
(function () {
    'use strict';

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

    // Stable per-dot noise, so dither and reveal order never flicker between renders
    function hash(i, j) {
        let h = Math.imul(i, 374761393) + Math.imul(j, 668265263);
        h = Math.imul(h ^ (h >>> 13), 1274126177);
        return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
    }

    const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
    const smooth = (t) => t * t * (3 - 2 * t);

    const imageCache = new Map();
    function loadImage(src) {
        if (!imageCache.has(src)) {
            imageCache.set(src, new Promise((resolve, reject) => {
                const img = new Image();
                img.decoding = 'async';
                img.onload = () => resolve(img);
                img.onerror = reject;
                img.src = src;
            }));
        }
        return imageCache.get(src);
    }

    // Procedural grayscale scenes, drawn on white. Dark = more ink.
    const scenes = {
        // Layered ridgelines, far ones lighter — atmospheric depth
        ridges(ctx, w, h) {
            ctx.fillStyle = '#fff';
            ctx.fillRect(0, 0, w, h);
            const layers = [
                { base: 0.42, amp: 0.10, tone: 215, seed: 1.3 },
                { base: 0.55, amp: 0.12, tone: 170, seed: 2.7 },
                { base: 0.70, amp: 0.10, tone: 120, seed: 4.1 },
                { base: 0.84, amp: 0.08, tone: 70, seed: 5.9 }
            ];
            for (const l of layers) {
                const g = ctx.createLinearGradient(0, h * (l.base - l.amp), 0, h);
                g.addColorStop(0, `rgb(${l.tone},${l.tone},${l.tone})`);
                g.addColorStop(1, `rgb(${l.tone + 30},${l.tone + 30},${l.tone + 30})`);
                ctx.fillStyle = g;
                ctx.beginPath();
                ctx.moveTo(0, h);
                for (let x = 0; x <= w; x += 2) {
                    const t = x / w;
                    const y = l.base
                        - l.amp * (0.55 * Math.sin(t * 5.1 + l.seed)
                        + 0.30 * Math.sin(t * 11.7 + l.seed * 2.1)
                        + 0.15 * Math.sin(t * 27.3 + l.seed * 3.7));
                    ctx.lineTo(x, y * h);
                }
                ctx.lineTo(w, h);
                ctx.closePath();
                ctx.fill();
            }
        },

        // Stacked coins / orbs with a soft floor shadow
        orb(ctx, w, h) {
            ctx.fillStyle = '#fff';
            ctx.fillRect(0, 0, w, h);
            const floor = ctx.createLinearGradient(0, h * 0.62, 0, h);
            floor.addColorStop(0, '#f4f4f4');
            floor.addColorStop(1, '#b8b8b8');
            ctx.fillStyle = floor;
            ctx.fillRect(0, h * 0.62, w, h * 0.38);

            const sphere = (cx, cy, r) => {
                const sh = ctx.createRadialGradient(cx + r * 0.25, cy + r * 1.02, 0, cx + r * 0.25, cy + r * 1.02, r * 1.3);
                sh.addColorStop(0, 'rgba(0,0,0,0.55)');
                sh.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.fillStyle = sh;
                ctx.beginPath();
                ctx.ellipse(cx + r * 0.25, cy + r * 1.02, r * 1.3, r * 0.28, 0, 0, Math.PI * 2);
                ctx.fill();
                const g = ctx.createRadialGradient(cx - r * 0.4, cy - r * 0.45, r * 0.05, cx, cy, r);
                g.addColorStop(0, '#fafafa');
                g.addColorStop(0.55, '#8a8a8a');
                g.addColorStop(1, '#1e1e1e');
                ctx.fillStyle = g;
                ctx.beginPath();
                ctx.arc(cx, cy, r, 0, Math.PI * 2);
                ctx.fill();
            };
            const m = Math.min(w, h);
            sphere(w * 0.62, h * 0.50, m * 0.24);
            sphere(w * 0.30, h * 0.62, m * 0.12);
        },

        // Rising columns, lit from the left
        bars(ctx, w, h) {
            ctx.fillStyle = '#fff';
            ctx.fillRect(0, 0, w, h);
            const n = 6;
            const gap = w * 0.035;
            const bw = (w * 0.78 - gap * (n - 1)) / n;
            const depth = bw * 0.35;
            const ground = h * 0.86;
            for (let i = 0; i < n; i++) {
                const x = w * 0.1 + i * (bw + gap);
                const bh = h * (0.18 + 0.1 * i + 0.05 * Math.sin(i * 2.3));
                const top = ground - bh;
                const front = ctx.createLinearGradient(0, top, 0, ground);
                front.addColorStop(0, '#9a9a9a');
                front.addColorStop(1, '#3a3a3a');
                ctx.fillStyle = front;
                ctx.fillRect(x, top, bw, bh);
                ctx.fillStyle = '#2a2a2a';
                ctx.beginPath();
                ctx.moveTo(x + bw, top);
                ctx.lineTo(x + bw + depth, top - depth * 0.6);
                ctx.lineTo(x + bw + depth, ground - depth * 0.6);
                ctx.lineTo(x + bw, ground);
                ctx.fill();
                ctx.fillStyle = '#e2e2e2';
                ctx.beginPath();
                ctx.moveTo(x, top);
                ctx.lineTo(x + depth, top - depth * 0.6);
                ctx.lineTo(x + bw + depth, top - depth * 0.6);
                ctx.lineTo(x + bw, top);
                ctx.fill();
            }
            const fl = ctx.createLinearGradient(0, ground, 0, h);
            fl.addColorStop(0, '#9c9c9c');
            fl.addColorStop(1, '#f0f0f0');
            ctx.fillStyle = fl;
            ctx.fillRect(0, ground, w, h - ground);
        },

        // A pendant lamp glowing in a dim room
        glow(ctx, w, h) {
            const bg = ctx.createLinearGradient(0, 0, 0, h);
            bg.addColorStop(0, '#5a5a5a');
            bg.addColorStop(1, '#2a2a2a');
            ctx.fillStyle = bg;
            ctx.fillRect(0, 0, w, h);
            const cx = w * 0.5;
            const cy = h * 0.42;
            const light = ctx.createRadialGradient(cx, cy + h * 0.12, 0, cx, cy + h * 0.12, Math.max(w, h) * 0.55);
            light.addColorStop(0, 'rgba(255,255,255,1)');
            light.addColorStop(0.35, 'rgba(255,255,255,0.55)');
            light.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.fillStyle = light;
            ctx.fillRect(0, 0, w, h);
            ctx.strokeStyle = '#1a1a1a';
            ctx.lineWidth = Math.max(1, w * 0.004);
            ctx.beginPath();
            ctx.moveTo(cx, 0);
            ctx.lineTo(cx, cy - h * 0.1);
            ctx.stroke();
            ctx.fillStyle = '#1c1c1c';
            ctx.beginPath();
            ctx.moveTo(cx - w * 0.05, cy - h * 0.1);
            ctx.lineTo(cx + w * 0.05, cy - h * 0.1);
            ctx.lineTo(cx + w * 0.14, cy + h * 0.06);
            ctx.lineTo(cx - w * 0.14, cy + h * 0.06);
            ctx.closePath();
            ctx.fill();
            ctx.fillStyle = '#fff';
            ctx.beginPath();
            ctx.ellipse(cx, cy + h * 0.06, w * 0.14, h * 0.018, 0, 0, Math.PI * 2);
            ctx.fill();
        }
    };

    class Halftone {
        constructor(el) {
            this.el = el;
            this.opts = this.readOptions();
            this.canvas = document.createElement('canvas');
            this.canvas.setAttribute('role', 'img');
            this.canvas.setAttribute('aria-label', el.dataset.alt || '');
            this.ctx = this.canvas.getContext('2d');
            el.appendChild(this.canvas);
            this.dots = null;
            this.progress = el.dataset.reveal === 'false' || reduceMotion.matches ? 1 : 0;
            this.pointer = null;
            this.pointerAmt = 0;
            this.frame = 0;
            this.source = null;

            if (el.hasAttribute('data-interactive') && !reduceMotion.matches) {
                el.addEventListener('pointermove', (e) => {
                    const r = this.canvas.getBoundingClientRect();
                    this.pointer = { x: e.clientX - r.left, y: e.clientY - r.top };
                    this.kick();
                });
                el.addEventListener('pointerleave', () => this.kick());
            }
        }

        readOptions() {
            const d = this.el.dataset;
            const small = window.matchMedia('(max-width: 640px)').matches;
            const [fx, fy] = (d.focus || '0.5 0.5').split(/\s+/).map(Number);
            return {
                cell: Number(d.cell) || (small ? 5 : 7),
                contrast: d.contrast ? Number(d.contrast) : 1.15,
                bright: d.bright ? Number(d.bright) : 0,
                sharpen: d.sharpen ? Number(d.sharpen) : 0.6,
                black: d.black ? Number(d.black) : 0.04,
                white: d.white ? Number(d.white) : 0.9,
                inset: d.inset ? Number(d.inset) : 0,
                fade: d.fade || 'none',
                fit: d.fit || 'cover',
                fx: isNaN(fx) ? 0.5 : fx,
                fy: isNaN(fy) ? 0.5 : fy,
                interactive: this.el.hasAttribute('data-interactive')
            };
        }

        async load() {
            const d = this.el.dataset;
            if (d.scene && scenes[d.scene]) {
                this.source = { scene: scenes[d.scene] };
            } else if (d.src) {
                this.source = { img: await loadImage(d.src) };
            }
            this.el.classList.add('is-ready');
        }

        // Downsample the source to one pixel per dot, then size each dot by its darkness
        compute() {
            const rect = this.el.getBoundingClientRect();
            const w = Math.max(1, Math.round(rect.width));
            const h = Math.max(1, Math.round(rect.height));
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            this.w = w;
            this.h = h;
            this.canvas.width = Math.round(w * dpr);
            this.canvas.height = Math.round(h * dpr);
            this.canvas.style.width = w + 'px';
            this.canvas.style.height = h + 'px';
            this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

            const o = this.opts;
            const cell = o.cell;
            const rowH = cell * Math.sqrt(3) / 2;
            const cols = Math.ceil(w / cell) + 1;
            const rows = Math.ceil(h / rowH) + 1;

            const sample = document.createElement('canvas');
            sample.width = cols;
            sample.height = rows;
            const sctx = sample.getContext('2d', { willReadFrequently: true });
            sctx.imageSmoothingQuality = 'high';

            if (this.source.scene) {
                // Draw scenes at display size, then scale down for even averaging
                const big = document.createElement('canvas');
                big.width = w;
                big.height = h;
                this.source.scene(big.getContext('2d'), w, h);
                sctx.drawImage(big, 0, 0, cols, rows);
            } else {
                const img = this.source.img;
                sctx.fillStyle = '#fff';
                sctx.fillRect(0, 0, cols, rows);
                const sx = img.naturalWidth * o.inset;
                const sy = img.naturalHeight * o.inset;
                const sw = img.naturalWidth - sx * 2;
                const sh = img.naturalHeight - sy * 2;
                const boxW = cols * cell;
                const boxH = rows * rowH;
                const scale = o.fit === 'contain'
                    ? Math.min(boxW / sw, boxH / sh)
                    : Math.max(boxW / sw, boxH / sh);
                const dw = sw * scale;
                const dh = sh * scale;
                const dx = (boxW - dw) * o.fx;
                const dy = (boxH - dh) * o.fy;
                sctx.drawImage(img, sx, sy, sw, sh, dx / cell, dy / rowH, dw / cell, dh / rowH);
            }

            const px = sctx.getImageData(0, 0, cols, rows).data;
            const lums = new Float32Array(cols * rows);
            for (let n = 0; n < lums.length; n++) {
                const k = n * 4;
                lums[n] = (0.2126 * px[k] + 0.7152 * px[k + 1] + 0.0722 * px[k + 2]) / 255;
            }
            const at = (i, j) => lums[Math.min(rows - 1, Math.max(0, j)) * cols + Math.min(cols - 1, Math.max(0, i))];
            const span = o.white - o.black;
            const gamma = Math.pow(2, -o.bright * 2.5);

            const rMax = cell * 0.5;
            const rMin = cell * 0.17;
            const dots = [];
            for (let j = 0; j < rows; j++) {
                const y = j * rowH;
                const offset = j % 2 ? cell / 2 : 0;
                let fade = 1;
                if (o.fade === 'top') fade = smooth(clamp01(y / (h * 0.45)));
                else if (o.fade === 'bottom') fade = smooth(clamp01((h - y) / (h * 0.45)));
                for (let i = 0; i < cols; i++) {
                    // Unsharp mask keeps outlines and facial features crisp at dot scale
                    const c = at(i, j);
                    const blur = (at(i - 1, j) + at(i + 1, j) + at(i, j - 1) + at(i, j + 1)) / 4;
                    const lum = c + (c - blur) * o.sharpen;
                    // Levels: near-white source backgrounds become clean paper
                    const lv = clamp01((lum - o.black) / span);
                    const l = Math.pow(clamp01((lv - 0.5) * o.contrast + 0.5), gamma);
                    const ink = (1 - l) * fade;
                    if (ink < 0.035) continue;
                    let r = rMax * Math.sqrt(ink);
                    const noise = hash(i, j);
                    // Light tones become sparse fixed-size dots instead of specks
                    if (r < rMin) {
                        if (noise > (r / rMin) * (r / rMin)) continue;
                        r = rMin;
                    }
                    dots.push({
                        x: i * cell + offset,
                        y,
                        r,
                        // reveal rises from the bottom with a ragged edge
                        t: (1 - y / h) * 0.75 + hash(j, i) * 0.25
                    });
                }
            }
            this.dots = dots;
        }

        draw() {
            const ctx = this.ctx;
            const ink = getComputedStyle(this.el).getPropertyValue('--ink').trim() || '#2f2f2d';
            ctx.clearRect(0, 0, this.w, this.h);
            ctx.fillStyle = ink;
            ctx.beginPath();
            const p = this.progress;
            const ptr = this.pointer;
            const amt = this.pointerAmt;
            const reach = this.opts.cell * 9;
            for (const d of this.dots) {
                let r = d.r;
                if (p < 1) {
                    const s = clamp01((p * 1.25 - d.t) / 0.25);
                    if (s <= 0) continue;
                    r *= s;
                }
                let x = d.x;
                let y = d.y;
                if (ptr && amt > 0) {
                    const dx = x - ptr.x;
                    const dy = y - ptr.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < reach) {
                        const f = smooth(1 - dist / reach) * amt;
                        const push = f * this.opts.cell * 1.6 / (dist || 1);
                        x += dx * push;
                        y += dy * push;
                        r *= 1 - f * 0.5;
                    }
                }
                ctx.moveTo(x + r, y);
                ctx.arc(x, y, r, 0, Math.PI * 2);
            }
            ctx.fill();
        }

        // Animate reveal and pointer easing; stop the loop when settled
        kick() {
            if (this.frame) return;
            const step = () => {
                this.frame = 0;
                let moving = false;
                if (this.progress < 1) {
                    this.progress = Math.min(1, this.progress + 1 / 75);
                    moving = true;
                }
                const target = this.el.matches(':hover') && this.pointer ? 1 : 0;
                if (Math.abs(this.pointerAmt - target) > 0.01) {
                    this.pointerAmt += (target - this.pointerAmt) * 0.12;
                    moving = true;
                } else {
                    this.pointerAmt = target;
                }
                this.draw();
                if (moving) this.frame = requestAnimationFrame(step);
            };
            this.frame = requestAnimationFrame(step);
        }

        refresh() {
            if (!this.source) return;
            this.opts = this.readOptions();
            this.compute();
            this.draw();
        }
    }

    function init(root) {
        const els = (root || document).querySelectorAll('.ht:not([data-ht-ready])');
        const io = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) continue;
                io.unobserve(entry.target);
                const ht = entry.target._ht;
                ht.load().then(() => {
                    ht.compute();
                    ht.draw();
                    ht.kick();
                }).catch(() => entry.target.classList.add('is-error'));
            }
        }, { rootMargin: '0px 0px -10% 0px' });

        const ro = new ResizeObserver((entries) => {
            for (const entry of entries) {
                const ht = entry.target._ht;
                clearTimeout(ht.resizeTimer);
                ht.resizeTimer = setTimeout(() => ht.refresh(), 120);
            }
        });

        els.forEach((el) => {
            el.dataset.htReady = '';
            el._ht = new Halftone(el);
            io.observe(el);
            ro.observe(el);
        });
    }

    window.Halftone = { init, scenes, Renderer: Halftone };
})();
