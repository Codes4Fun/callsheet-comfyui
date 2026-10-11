import numpy as np
import torch
from scipy import signal


class CallsheetAudioFX:
    """Audio effects using scipy - stable and fast."""

    ENVIRONMENTS = {
        'outdoor':  {'hp': 80,  'lp': 8000, 'reverb': {'room': 0.1, 'damping': 0.1, 'wet': 0.15}},
        'hall':     {'hp': 40,  'lp': None, 'reverb': {'room': 0.6, 'damping': 0.4, 'wet': 0.35}},
        'bathroom': {'hp': 100, 'lp': 10000,'reverb': {'room': 0.2, 'damping': 0.1, 'wet': 0.30},
                     'peak': (800, 6)},
        'cave':     {'hp': 60,  'lp': 8000, 'reverb': {'room': 0.9, 'damping': 0.8, 'wet': 0.45}},
        'car':      {'hp': 120, 'lp': 4000, 'reverb': {'room': 0.15,'damping': 0.3, 'wet': 0.20}},
        'distant':  {'hp': 200, 'lp': 4000, 'reverb': {'room': 0.05,'damping': 0.2, 'wet': 0.10},
                     'gain': -6},
        'close':    {'hp': 60,  'lp': None, 'reverb': {'room': 0.05,'damping': 0.5, 'wet': 0.05}},
        'intimate': {'hp': 80,  'lp': 8000, 'reverb': {'room': 0.02,'damping': 0.7, 'wet': 0.02}},
    }

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "instructions": ("STRING", {"multiline": True, "default": "outdoor, -3dB"}),
                "sample_rate": ("INT", {"default": 0, "min": 0, "max": 384000}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "normalize": ("BOOLEAN", {"default": False}),
                "silence_threshold": ("FLOAT", {"default": -50.0, "min": -120.0, "max": 0.0, "step": 1.0}),
            },
            "optional": {
                "audio_1": ("AUDIO",),
                "output_channels": (["mono", "stereo", "stereo_wide"], {"default": "mono"}),
            }
        }

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "process"
    CATEGORY = "audio/effects"

    # ------------------------------------------------------------------ helpers

    def generate_ir(self, sr, room=0.5, damping=0.5, length=1.0, seed=0):
        rng = np.random.RandomState(seed)
        samples = int(sr * length)
        t = np.linspace(0, length, samples)
        decay = np.exp(-t * 3 / max(room, 0.01))
        damping_curve = np.exp(-t * damping * 5)
        reflections = rng.uniform(-1, 1, samples)
        ir = decay * damping_curve * reflections
        pre_delay = int(room * 0.05 * sr)
        if pre_delay < samples:
            ir[pre_delay] += 0.3
        peak = np.max(np.abs(ir))
        if peak > 0:
            ir = ir / peak * 0.5
        return ir.astype(np.float32)

    def _to_numpy(self, audio):
        wf = audio["waveform"] if isinstance(audio, dict) else audio
        if isinstance(wf, torch.Tensor):
            arr = wf.detach().cpu().numpy()
        else:
            arr = np.asarray(wf)
        arr = np.asarray(arr, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr[None, :]
        elif arr.ndim == 3:
            arr = arr[0] if arr.shape[0] == 1 else np.mean(arr, axis=0)
        return np.atleast_2d(arr)

    def _match_channels(self, arr, target_ch):
        ch = arr.shape[0]
        if ch == target_ch:
            return arr
        if ch == 1:
            return np.repeat(arr, target_ch, axis=0)
        if target_ch == 1:
            return np.mean(arr, axis=0, keepdims=True)
        if ch > target_ch:
            return arr[:target_ch]
        reps = int(np.ceil(target_ch / ch))
        return np.tile(arr, (reps, 1))[:target_ch]

    def _parse_count(self, spec, sr):
        spec = spec.strip().lower()
        if spec.endswith('s'):
            return int(round(float(spec[:-1]) * sr))
        return int(round(float(spec)))

    def _parse_level(self, spec):
        spec = spec.strip().lower()
        if spec.endswith('%'):
            return float(spec[:-1]) / 100.0
        if spec.endswith('db'):
            return 10 ** (float(spec[:-2]) / 20.0)
        return float(spec)

    def _parse_envelope(self, tokens, sr):
        if len(tokens) % 2 != 0:
            raise ValueError("envelope needs level/duration pairs")
        pts = []
        for i in range(0, len(tokens), 2):
            pts.append((self._parse_level(tokens[i]), self._parse_count(tokens[i + 1], sr)))
        return pts

    def _apply_envelope(self, arr, points):
        n = arr.shape[-1]
        if not points:
            return arr
        gain = np.ones(n, dtype=np.float32)
        pos = 0
        cur = points[0][0]
        end = min(pos + points[0][1], n)
        gain[pos:end] = cur
        pos = end
        for lvl, dur in points[1:]:
            if pos >= n:
                break
            if dur <= 0:
                cur = lvl
                continue
            end = min(pos + dur, n)
            if end > pos:
                gain[pos:end] = np.linspace(cur, lvl, end - pos, endpoint=False)
            cur = lvl
            pos = end
        if pos < n:
            gain[pos:] = cur
        return arr * gain

    def _silence_trim(self, arr, sr, threshold_db):
        thresh = 10 ** (threshold_db / 20.0)
        env = np.max(np.abs(arr), axis=0)          # max across channels keeps them in sync
        win = max(1, int(0.01 * sr))               # 10ms smoothing
        if win > 1:
            env = np.convolve(env, np.ones(win) / win, mode='same')
        loud = np.where(env > thresh)[0]
        if loud.size == 0:
            return arr
        return arr[..., loud[0]:loud[-1] + 1]

    def _convolve(self, bus, ir_mono, wet):
        out = np.empty_like(bus)
        for c in range(bus.shape[0]):
            w = signal.fftconvolve(bus[c], ir_mono, mode='full')[:bus.shape[-1]]
            out[c] = (1 - wet) * bus[c] + wet * w
        return out

    # ------------------------------------------------------------------ parsing

    def _parse_reverb(self, tokens):
        ref = None
        if tokens and tokens[0].startswith('@'):
            ref = int(tokens[0][1:])
            tokens = tokens[1:]
        if len(tokens) == 3:
            room, damping, wet = (float(t) for t in tokens)
            return {'type': 'reverb', 'synthetic': True, 'room': room, 'damping': damping, 'wet': wet}
        if len(tokens) == 1:
            return {'type': 'reverb', 'synthetic': False, 'wet': float(tokens[0]), 'track': ref}
        if len(tokens) == 0 and ref is not None:
            return {'type': 'reverb', 'synthetic': False, 'wet': 0.5, 'track': ref}
        raise ValueError("reverb wants 'wet', 'room damping wet', or '@track [wet]'")

    def _parse_track(self, tokens, sr):
        ref = None
        if tokens and tokens[0].startswith('@'):
            ref = int(tokens[0][1:])
            tokens = tokens[1:]
        if not tokens:
            raise ValueError("track needs a start time")
        start = self._parse_count(tokens[0], sr)
        env = self._parse_envelope(tokens[1:], sr) if len(tokens) > 1 else None
        return {'type': 'track', 'start': start, 'track': ref, 'envelope': env}

    def _parse_append(self, tokens, sr):
        ref = None
        if tokens and tokens[0].startswith('@'):
            ref = int(tokens[0][1:])
            tokens = tokens[1:]
        env = self._parse_envelope(tokens, sr) if tokens else None
        return {'type': 'append', 'track': ref, 'envelope': env}

    def parse_instructions(self, text, sr):
        steps, errors = [], []
        text = (text or "").lower().strip()
        if not text:
            return steps, errors
        for part in [p.strip() for p in text.split(',') if p.strip()]:
            tokens = part.split()
            head, rest = tokens[0], tokens[1:]
            if ':' in head:
                name, _, inline = head.partition(':')
                rest = ([inline] if inline else []) + rest
            else:
                name = head
            try:
                if name in self.ENVIRONMENTS:
                    steps.append({'type': 'preset', 'name': name})
                elif name in ('hp', 'highpass'):
                    steps.append({'type': 'highpass', 'freq': float(rest[0])})
                elif name in ('lp', 'lowpass'):
                    steps.append({'type': 'lowpass', 'freq': float(rest[0])})
                elif name == 'reverb':
                    steps.append(self._parse_reverb(rest))
                elif name == 'track':
                    steps.append(self._parse_track(rest, sr))
                elif name == 'append':
                    steps.append(self._parse_append(rest, sr))
                elif name == 'volume':
                    steps.append({'type': 'volume', 'points': self._parse_envelope(rest, sr)})
                elif name == 'pad':
                    steps.append({'type': 'pad', 'samples': self._parse_count(rest[0], sr)})
                elif name == 'trim':
                    if rest:
                        steps.append({'type': 'trim', 'samples': self._parse_count(rest[0], sr)})
                    else:
                        steps.append({'type': 'trim', 'silence': True, 'samples': 0})
                elif name == 'crop':
                    steps.append({'type': 'crop', 'samples': self._parse_count(rest[0], sr)})
                elif name == 'resize':
                    steps.append({'type': 'resize', 'samples': self._parse_count(rest[0], sr)})
                elif name == 'normalize':
                    on = True if not rest else rest[0] in ('on', 'true', '1', 'yes')
                    steps.append({'type': 'normalize', 'on': on})
                else:
                    steps.append({'type': 'gain', 'lin': self._parse_level(name)})
            except Exception as e:
                errors.append(f"bad instruction '{part}': {e}")
        return steps, errors

    # ------------------------------------------------------------------ process

    def process(self, audio, instructions, sample_rate=0, seed=0, normalize=False,
                silence_threshold=-50.0, output_channels="mono", **kwargs):

        # tracks[0] = base audio, tracks[1..] = connected audio_N (sorted by index)
        extra = []
        for k, v in kwargs.items():
            if k.startswith("audio_"):
                try:
                    idx = int(k.split("_", 1)[1])
                except ValueError:
                    continue
                if v is not None:
                    extra.append((idx, v))
        extra.sort(key=lambda t: t[0])

        base = self._to_numpy(audio)
        if sample_rate == 0:
            sample_rate = audio.get("sample_rate", 44100) if isinstance(audio, dict) else 44100

        bus = base.copy()
        bus_ch = bus.shape[0]
        tracks = [base] + [self._match_channels(self._to_numpy(t), bus_ch) for _, t in extra]

        steps, errors = self.parse_instructions(instructions, sample_rate)
        if errors:
            raise RuntimeError("CallsheetAudioFX instruction errors:\n  " + "\n  ".join(errors))

        next_track = 1
        used = set()

        def consume(ref):
            nonlocal next_track
            if ref is not None:
                if ref < 0 or ref >= len(tracks):
                    raise RuntimeError(f"@track {ref} out of range (available 0..{len(tracks) - 1})")
                used.add(ref)
                return ref
            if next_track >= len(tracks):
                raise RuntimeError(
                    f"instruction needs another audio track but only {len(tracks) - 1} extra track(s) connected")
            idx = next_track
            next_track += 1
            used.add(idx)
            return idx

        for step in steps:
            t = step['type']

            if t == 'preset':
                env = self.ENVIRONMENTS[step['name']]
                if env.get('hp'):
                    sos = signal.butter(4, env['hp'], 'hp', fs=sample_rate, output='sos')
                    bus = signal.sosfilt(sos, bus, axis=-1)
                if env.get('lp'):
                    sos = signal.butter(4, env['lp'], 'lp', fs=sample_rate, output='sos')
                    bus = signal.sosfilt(sos, bus, axis=-1)
                if env.get('peak'):
                    freq, gain = env['peak']
                    b, a = signal.iirpeak(freq / (sample_rate / 2), 10)
                    filt = signal.lfilter(b, a, bus, axis=-1)
                    mix = min(abs(gain) / 20, 0.5)
                    bus = bus + (mix if gain > 0 else -mix) * filt
                if env.get('reverb'):
                    r = env['reverb']
                    ir = self.generate_ir(sample_rate, r['room'], r['damping'], seed=seed)
                    bus = self._convolve(bus, ir, r['wet'])
                if env.get('gain'):
                    bus = bus * (10 ** (env['gain'] / 20))

            elif t == 'highpass':
                sos = signal.butter(4, step['freq'], 'hp', fs=sample_rate, output='sos')
                bus = signal.sosfilt(sos, bus, axis=-1)

            elif t == 'lowpass':
                sos = signal.butter(4, step['freq'], 'lp', fs=sample_rate, output='sos')
                bus = signal.sosfilt(sos, bus, axis=-1)

            elif t == 'gain':
                bus = bus * step['lin']

            elif t == 'reverb':
                if step['synthetic']:
                    print(f"!!!!!! synthetic {step['wet']}")
                    ir = self.generate_ir(sample_rate, step['room'], step['damping'], seed=seed)
                else:
                    idx = consume(step.get('track'))
                    print(f"!!!!!! ir wav {idx} {tracks[idx].shape}")
                    ir = np.mean(tracks[idx], axis=0)  # mono IR
                bus = self._convolve(bus, ir, step['wet'])

            elif t == 'volume':
                bus = self._apply_envelope(bus, step['points'])

            elif t == 'track':
                # place into the bus window; crop whatever falls outside
                idx = consume(step.get('track'))
                trk = tracks[idx].copy()
                if step['envelope']:
                    trk = self._apply_envelope(trk, step['envelope'])
                start = step['start']
                n = bus.shape[-1]
                dst = max(start, 0)
                src = dst - start
                length = min(trk.shape[-1] - src, n - dst)
                if length > 0:
                    bus[:, dst:dst + length] += trk[:, src:src + length]

            elif t == 'append':
                # extend the bus by the track's length
                idx = consume(step.get('track'))
                trk = tracks[idx].copy()
                if step['envelope']:
                    trk = self._apply_envelope(trk, step['envelope'])
                bus = np.concatenate([bus, trk], axis=-1)

            elif t == 'pad':
                n = abs(step['samples'])
                if n:
                    pad = np.zeros((bus.shape[0], n), dtype=bus.dtype)
                    bus = np.concatenate([pad, bus], axis=-1) if step['samples'] > 0 \
                        else np.concatenate([bus, pad], axis=-1)

            elif t == 'trim':
                if step.get('silence'):
                    bus = self._silence_trim(bus, sample_rate, silence_threshold)
                else:
                    n = step['samples']
                    if n > 0:
                        bus = bus[..., n:]
                    elif n < 0:
                        bus = bus[..., :n]

            elif t == 'crop':
                n = step['samples']
                if n > 0:
                    bus = bus[..., :n]
                elif n < 0:
                    bus = bus[..., n:]

            elif t == 'resize':
                signed = step['samples']
                target = abs(signed)
                n = bus.shape[-1]
                if signed >= 0:
                    if n > target:
                        bus = bus[..., :target]
                    elif n < target:
                        pad = np.zeros((bus.shape[0], target - n), dtype=bus.dtype)
                        bus = np.concatenate([bus, pad], axis=-1)
                else:
                    if n > target:
                        bus = bus[..., n - target:]
                    elif n < target:
                        pad = np.zeros((bus.shape[0], target - n), dtype=bus.dtype)
                        bus = np.concatenate([pad, bus], axis=-1)

            elif t == 'normalize':
                normalize = step['on']

        for i in range(1, len(tracks)):
            if i not in used:
                print(f"[CallsheetAudioFX] warning: audio track {i} is connected but never used")

        if output_channels == "mono":
            out = np.mean(bus, axis=0, keepdims=True)
        elif output_channels == "stereo":
            out = self._match_channels(bus, 2)
        else:  # stereo_wide
            left = self._match_channels(bus, 1)[0]
            delay = int(0.015 * sample_rate)
            right = np.zeros_like(left)
            if delay:
                right[delay:] = left[:-delay] * 0.95
            else:
                right = left.copy()
            b, a = signal.butter(2, 6000 / (sample_rate / 2), 'low')
            right = signal.lfilter(b, a, right)
            out = np.stack([left, right])

        if normalize:
            print(f"!!!!! normalizing")
            peak = np.max(np.abs(out))
            if peak > 0:
                out = out / peak * 0.95
        else:
            print(f"!!!!! not normalizing")

        out = np.atleast_2d(out).astype(np.float32)
        waveform = torch.from_numpy(out[None, :, :])
        print(f"!!!! audio_fx: waveform {waveform.shape} {waveform.dtype}")
        return ({"waveform": waveform, "sample_rate": sample_rate},)

