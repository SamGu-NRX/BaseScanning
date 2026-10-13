/// Metal source, compiled at launch with `makeLibrary(source:)` so `swift build` needs no
/// Metal toolchain step. Every value the shader bakes in matches `Tuning`.
enum Shaders {
    static let source = """
    #include <metal_stdlib>
    using namespace metal;

    // cubic-bezier(0.23, 1, 0.32, 1): solve x(t) = p by Newton, return y(t). Same as CubicBezier.swift.
    static float strongEaseOut(float p) {
        p = clamp(p, 0.0f, 1.0f);
        if (p <= 0.0f || p >= 1.0f) { return p; }
        const float x1 = 0.23f, x2 = 0.32f, y1 = 1.0f, y2 = 1.0f;
        float t = p;
        for (int i = 0; i < 8; i++) {
            float u = 1.0f - t;
            float x = 3.0f * u * u * t * x1 + 3.0f * u * t * t * x2 + t * t * t - p;
            float dx = 3.0f * u * u * x1 + 6.0f * u * t * (x2 - x1) + 3.0f * t * t * (1.0f - x2);
            if (fabs(dx) < 1e-6f) { break; }
            t = clamp(t - x / dx, 0.0f, 1.0f);
        }
        float u = 1.0f - t;
        return 3.0f * u * u * t * y1 + 3.0f * u * t * t * y2 + t * t * t;
    }

    // MARK: Camera image

    struct CameraUniforms {
        float2 offset;     // pixels: top-left of the portrait image on screen
        float scale;       // screen pixels per portrait image pixel
        float pad;
        float2 imageSize;  // landscape image size
    };

    struct CameraOut { float4 position [[position]]; };

    vertex CameraOut cameraVertex(uint vid [[vertex_id]]) {
        float2 p = float2((vid << 1) & 2, vid & 2);
        CameraOut out;
        out.position = float4(p * 2.0f - 1.0f, 0.5f, 1.0f);
        return out;
    }

    // Screen pixel -> portrait pixel -> landscape pixel (turned back 90 degrees counterclockwise).
    fragment float4 cameraFragment(CameraOut in [[stage_in]],
                                   constant CameraUniforms& u [[buffer(0)]],
                                   texture2d<float> image [[texture(0)]]) {
        constexpr sampler s(filter::linear, address::clamp_to_edge);
        float2 portrait = (in.position.xy - u.offset) / u.scale;
        float2 landscape = float2(portrait.y, u.imageSize.y - portrait.x);
        return float4(image.sample(s, landscape / u.imageSize).rgb, 1.0f);
    }

    // MARK: Fog comparison veil

    vertex float4 veilVertex(uint vid [[vertex_id]],
                             const device float4* positions [[buffer(0)]],
                             constant float4x4& clip [[buffer(1)]]) {
        return clip * positions[vid];
    }

    fragment float4 veilFragment() {
        return float4(0.0f, 0.0f, 0.0f, 0.35f);
    }

    // MARK: Dots

    struct Sprite {
        float4 a;  // world xyz, 1 for a simulated feature point (fixed size), else 0
        float4 b;  // birth time, from opacity, to opacity, opacity change time
        float4 c;  // edge since, death time, violet (0 or 1), 1 for the halo sprite
    };

    struct DotUniforms {
        float4x4 clip;
        float4 cameraAndTime;  // camera xyz, playback time
        float pointScale;      // pixels per point
        float reduceMotion;    // 1 drops the birth scale
        float scheme;          // 0 hologram, 1 constellation, 2 ember
        float pad;
    };

    constant float3 hologram = float3(0xE6, 0xEC, 0xF4) / 255.0f;
    constant float3 glow = float3(0x9C, 0xC8, 0xFF) / 255.0f;
    constant float3 violet = float3(0xB4, 0x9C, 0xFF) / 255.0f;
    constant float3 emberCore = float3(0xFF, 0xB4, 0x54) / 255.0f;
    constant float3 emberHalo = float3(0xFF, 0xC9, 0x78) / 255.0f;

    // Constellation hides flat dots, so a dot that turns edge is born then, not at its voxel's birth.
    // Feature points never become edges, and their edge time of 1e6 s would hide them for good.
    static float visibleBirth(Sprite s, int scheme) {
        return scheme == 1 && s.a.w < 0.5f ? max(s.b.x, s.c.x) : s.b.x;
    }

    struct DotOut {
        float4 position [[position]];
        float size [[point_size]];
        float4 color;
        float inner;  // radius fraction where the soft edge starts
    };

    vertex DotOut dotVertex(uint vid [[vertex_id]],
                            const device Sprite* sprites [[buffer(0)]],
                            constant DotUniforms& u [[buffer(1)]]) {
        Sprite s = sprites[vid];
        float t = u.cameraAndTime.w;
        int scheme = int(u.scheme + 0.5f);
        float birth = strongEaseOut((t - visibleBirth(s, scheme)) / 0.35f);
        float evidence = mix(s.b.y, s.b.z, strongEaseOut((t - s.b.w) / 0.25f));
        // Ember: amber at 90% at birth, cooling linearly to the evidence opacity over 6 s.
        float warmth = scheme == 2 ? clamp(1.0f - (t - s.b.x) / 6.0f, 0.0f, 1.0f) : 0.0f;
        evidence = mix(evidence, 0.9f, warmth);
        float fade = 1.0f - strongEaseOut((t - s.c.y) / 0.25f);
        float edge = strongEaseOut((t - s.c.x) / 0.25f);
        float alpha = evidence * birth * fade;
        bool feature = s.a.w > 0.5f;
        bool halo = s.c.w > 0.5f;

        // Flat 2.5 pt and edge 3.5 pt from 2.5 m out, growing linearly to 4 and 6 pt at 1 m;
        // constellation edges 4.5 pt growing to 7.
        float size = 4.5f;
        if (!feature) {
            float far = clamp((length(s.a.xyz - u.cameraAndTime.xyz) - 1.0f) / 1.5f, 0.0f, 1.0f);
            size = scheme == 1 ? mix(7.0f, 4.5f, far) : mix(mix(4.0f, 2.5f, far), mix(6.0f, 3.5f, far), edge);
        }
        float inner = feature ? 0.3f : 0.7f;
        float3 color = hologram;
        float3 warm = emberCore;
        if (halo) {
            // Edge 4x at 22%, flat 2x at 8%, feature 3x at 18%; a flat dot turning edge grows into it.
            size *= feature ? 3.0f : mix(2.0f, 4.0f, edge);
            alpha *= feature ? 0.18f : mix(0.08f, 0.22f, edge);
            inner = 0.0f;
            color = glow;
            warm = emberHalo;
        }
        color = mix(mix(color, violet, s.c.z), warm, warmth);
        float scale = u.reduceMotion > 0.5f ? 1.0f : mix(0.6f, 1.0f, birth);

        DotOut out;
        out.position = u.clip * float4(s.a.xyz, 1.0f);
        if (alpha < 1.0f / 512.0f) { out.position = float4(0.0f, 0.0f, -1.0f, 1.0f); }
        out.size = size * scale * u.pointScale;
        out.color = float4(color, 1.0f) * alpha;
        out.inner = inner;
        return out;
    }

    // MARK: Constellation links

    struct LinkOut {
        float4 position [[position]];
        float4 color;
    };

    // Each link vertex carries the link's timing (latest birth, earliest death, lower opacity of
    // its two dots), so the line appears and fades with the dots it joins.
    vertex LinkOut linkVertex(uint vid [[vertex_id]],
                              const device Sprite* sprites [[buffer(0)]],
                              constant DotUniforms& u [[buffer(1)]]) {
        Sprite s = sprites[vid];
        float t = u.cameraAndTime.w;
        float birth = strongEaseOut((t - s.b.x) / 0.35f);
        float evidence = mix(s.b.y, s.b.z, strongEaseOut((t - s.b.w) / 0.25f));
        float fade = 1.0f - strongEaseOut((t - s.c.y) / 0.25f);
        LinkOut out;
        out.position = u.clip * float4(s.a.xyz, 1.0f);
        out.color = float4(mix(glow, violet, s.c.z), 1.0f) * (0.35f * evidence * birth * fade);
        return out;
    }

    fragment float4 linkFragment(LinkOut in [[stage_in]]) {
        return in.color;
    }

    // Round sprite, fully opaque inside `inner`, fading to zero at the rim. Halos use 0: a glow.
    fragment float4 dotFragment(DotOut in [[stage_in]], float2 pc [[point_coord]]) {
        float r = length(pc * 2.0f - 1.0f);
        return in.color * (1.0f - smoothstep(in.inner, 1.0f, r));
    }

    // MARK: Fog
    //
    // The unmeasured world sits under one continuous fog that dissolves where dots arrive. Each
    // frame every live dot splats a soft disc (0.35 m, 6 to 30 mask pixels) into a 96 x 208
    // reveal mask; a radius-5 Gaussian blur and smoothstep(0.12, 0.55) feather its edge; a lagged
    // copy eases toward it so the fog lifts a beat after the dots appear (600 ms time constant,
    // a linear 400 ms fade under Reduce Motion) and returns quickly (120 ms) where a new view is
    // unmeasured. The fog itself is Chalk mixed toward a cool
    // grey, slightly thinner up close, textured by slow domain-warped fbm.
    //
    // Borrowed: the noise-edged fog-of-war mask from Civilization VI (lexdev.net case study); the
    // low-resolution mask sampled bilinearly after a blur (Andrew Hung, "Implementing Attractive
    // Fog of War in Unity"); reveal-by-measurement from Polycam's blue grid turning into white
    // mesh and Scaniverse's hatching over unscanned areas; the live point cloud and capture dial
    // of Apple's Object Capture; RoomPlan's coaching line and animated outlines (the boxes and
    // guidance, drawn in SwiftUI).

    struct SplatUniforms {
        float4x4 clip;
        float pixelsPerMetre;  // mask pixels per metre at 1 m
        float time;            // playback seconds
        float2 pad;
    };

    struct SplatOut {
        float4 position [[position]];
        float size [[point_size]];
        float weight;
    };

    vertex SplatOut splatVertex(uint vid [[vertex_id]],
                                const device Sprite* sprites [[buffer(0)]],
                                constant SplatUniforms& u [[buffer(1)]]) {
        Sprite s = sprites[vid];
        float t = u.time;
        float birth = strongEaseOut((t - s.b.x) / 0.35f);
        float evidence = mix(s.b.y, s.b.z, strongEaseOut((t - s.b.w) / 0.25f));
        float fade = 1.0f - strongEaseOut((t - s.c.y) / 0.25f);
        float factor = clamp((evidence - 0.45f) / 0.45f, 0.0f, 1.0f);
        SplatOut out;
        out.weight = birth * fade * (0.6f + 0.4f * factor);
        out.position = u.clip * float4(s.a.xyz, 1.0f);
        float radius = clamp(0.35f * u.pixelsPerMetre / max(out.position.w, 0.05f), 6.0f, 30.0f);
        out.size = 2.0f * radius;
        if (out.weight < 1.0f / 512.0f || out.position.w < 0.05f) { out.position = float4(0.0f, 0.0f, -1.0f, 1.0f); }
        return out;
    }

    fragment float4 splatFragment(SplatOut in [[stage_in]], float2 pc [[point_coord]]) {
        float2 d = pc * 2.0f - 1.0f;
        float k = saturate(1.0f - dot(d, d));
        return float4(in.weight * k * k, 0.0f, 0.0f, 0.0f);
    }

    // Separable Gaussian, radius 5 texels (sigma 2.5). The second pass clamps to 1 and applies
    // smoothstep(0.12, 0.55), the same as FogMask.shape.
    fragment float4 blurFragment(CameraOut in [[stage_in]],
                                 texture2d<float> source [[texture(0)]],
                                 constant int2& direction [[buffer(0)]],
                                 constant int& finish [[buffer(1)]]) {
        int2 p = int2(in.position.xy);
        int2 limit = int2(source.get_width() - 1, source.get_height() - 1);
        float sum = 0.0f, total = 0.0f;
        for (int k = -5; k <= 5; k++) {
            float w = exp(-float(k * k) / (2.0f * 2.5f * 2.5f));
            sum += w * source.read(uint2(clamp(p + k * direction, int2(0), limit))).r;
            total += w;
        }
        float v = sum / total;
        if (finish != 0) { v = smoothstep(0.12f, 0.55f, min(v, 1.0f)); }
        return float4(v, 0.0f, 0.0f, 0.0f);
    }

    struct LagUniforms {
        float dt;
        int reduceMotion;
        int reset;
        int pad;
    };

    // Same rule as FogMask.lagStep.
    fragment float4 lagFragment(CameraOut in [[stage_in]],
                                texture2d<float> target [[texture(0)]],
                                texture2d<float> previous [[texture(1)]],
                                constant LagUniforms& u [[buffer(0)]]) {
        uint2 p = uint2(in.position.xy);
        float goal = target.read(p).r;
        if (u.reset != 0) { return float4(goal, 0.0f, 0.0f, 0.0f); }
        float last = previous.read(p).r;
        bool lifting = goal > last;
        float next;
        if (u.reduceMotion != 0) {
            float step = u.dt / (lifting ? 0.4f : 0.12f);
            next = last + clamp(goal - last, -step, step);
        } else {
            next = last + (goal - last) * (1.0f - exp(-u.dt / (lifting ? 0.6f : 0.12f)));
        }
        return float4(next, 0.0f, 0.0f, 0.0f);
    }

    struct FogUniforms {
        float2 viewSize;   // pixels
        float2 offset;     // camera image placement, as CameraUniforms
        float scale;
        float time;        // ambient seconds, runs while paused
        float2 imageSize;
        float motion;      // 0 under Reduce Motion: no drift, no breathing
        float pad0;
        float2 pad1;
    };

    static float hash21(float2 p) {
        p = fract(p * float2(123.34f, 456.21f));
        p += dot(p, p + 45.32f);
        return fract(p.x * p.y);
    }

    static float valueNoise(float2 p) {
        float2 i = floor(p), f = fract(p);
        float2 w = f * f * (3.0f - 2.0f * f);
        float a = hash21(i), b = hash21(i + float2(1, 0)), c = hash21(i + float2(0, 1)), d = hash21(i + float2(1, 1));
        return mix(mix(a, b, w.x), mix(c, d, w.x), w.y);
    }

    static float fbm(float2 p) {
        float sum = 0.0f, amplitude = 0.5f;
        for (int i = 0; i < 3; i++) {
            sum += amplitude * valueNoise(p);
            p = p * 2.03f + float2(17.1f, 9.2f);
            amplitude *= 0.5f;
        }
        return sum / 0.875f;
    }

    fragment float4 fogFragment(CameraOut in [[stage_in]],
                                constant FogUniforms& u [[buffer(0)]],
                                texture2d<float> reveal [[texture(0)]],
                                texture2d<float> depth [[texture(1)]]) {
        constexpr sampler linear(filter::linear, address::clamp_to_edge);
        constexpr sampler nearest(filter::nearest, address::clamp_to_edge);
        float2 screen = in.position.xy;
        float revealed = reveal.sample(linear, screen / u.viewSize).r;

        float2 portrait = (screen - u.offset) / u.scale;
        float2 landscape = float2(portrait.y, u.imageSize.y - portrait.x);
        float d = depth.sample(nearest, landscape / u.imageSize).r;
        if (d <= 0.0f) { d = 100.0f; }  // no hit: far
        float depthTerm = 0.75f + 0.25f * smoothstep(1.0f, 4.5f, d);

        // uv in screen widths, so the noise is round, not stretched down the tall screen.
        float2 uv = screen / u.viewSize.x;
        float2 q = uv * 2.2f + u.motion * u.time * 0.02f * float2(0.94f, 0.34f);
        float2 warp = float2(fbm(q + float2(3.1f, 1.7f)), fbm(q + float2(-2.3f, 5.2f)));
        float noiseTerm = 0.78f + 0.22f * fbm(q + 0.8f * warp);
        float breathing = 1.0f + u.motion * 0.04f * sin(2.0f * M_PI_F * 0.08f * u.time);

        float alpha = saturate(0.62f * depthTerm * noiseTerm * (1.0f - revealed) * breathing);
        float3 chalk = float3(0xF7, 0xF5, 0xEF) / 255.0f;
        float3 cool = float3(0xD9, 0xE2, 0xEC) / 255.0f;
        return float4(mix(chalk, cool, 0.35f) * alpha, alpha);
    }
    """
}
