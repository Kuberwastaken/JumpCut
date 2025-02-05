import librosa
import numpy as np
from scipy.signal import find_peaks, savgol_filter
import os
from utils import log_error, download_audio, convert_audio_to_wav

def detect_drops(y, sr):
    """Enhanced drop detection using chroma features and adaptive thresholding."""
    try:
        hop_length = 512
        frame_length = 2048

        # Core features
        rms = librosa.feature.rms(y=y, hop_length=hop_length, frame_length=frame_length)[0]
        mel_spec = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop_length)
        mel_db = librosa.power_to_db(mel_spec, ref=np.max)
        spectral_contrast = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=hop_length)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length, aggregate=np.median)
        chroma = np.mean(librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop_length), axis=0)

        # Feature derivatives
        rms_diff = np.diff(rms, prepend=rms[0])
        mel_diff = np.diff(np.mean(mel_db, axis=0), prepend=0)
        contrast_diff = np.diff(np.mean(spectral_contrast, axis=0), prepend=0)
        chroma_diff = np.diff(chroma, prepend=0)

        # Normalize and combine features
        combined = (
            librosa.util.normalize(rms_diff) * 2.0 +
            librosa.util.normalize(mel_diff) * 1.5 +
            librosa.util.normalize(contrast_diff) * 1.2 +
            librosa.util.normalize(chroma_diff) * 1.0 +
            librosa.util.normalize(onset_env) * 1.8
        )

        # Smooth the curve
        smooth_diff = savgol_filter(combined, 15, 3)

        return smooth_diff, hop_length

    except Exception as e:
        log_error(f"Error in detect_drops: {str(e)}")
        return None, None

def find_best_drop(diff_curve, hop_length, sr, duration):
    """Find trendy segments using adaptive thresholding and silence detection."""
    try:
        # Adaptive threshold based on rolling variance
        window_size = 50
        rolling_var = np.array([np.var(diff_curve[max(0, i-window_size):i+1]) for i in range(len(diff_curve))])
        threshold = np.median(diff_curve) + (1.5 * np.median(rolling_var))

        # Peak detection with dynamic prominence
        peaks, properties = find_peaks(
            diff_curve, 
            height=threshold, 
            distance=int(1.5 * sr / hop_length), 
            prominence=np.percentile(diff_curve, 75) * 0.5, 
            width=10
        )

        if not peaks.size:
            return None

        peak_times = librosa.frames_to_time(peaks, sr=sr, hop_length=hop_length)
        prominences = properties['prominences']

        # Filter segments based on prominence
        segments = []
        for time, prom in zip(peak_times, prominences):
            position_weight = 1.0 - abs((time / duration) - 0.5)
            score = prom * position_weight
            start = max(time - 5, 0)
            end = min(time + 10, duration)
            segments.append((start, end, score))

        if not segments:
            return None

        # Sort by score and take top segments
        segments.sort(key=lambda x: x[2], reverse=True)
        top_segments = segments[:3]

        # Merge adjacent segments within 3-5s
        merged = []
        for start, end, _ in top_segments:
            if not merged or start > merged[-1][1] + 5:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)

        return (min(s[0] for s in merged), max(s[1] for s in merged))

    except Exception as e:
        log_error(f"Error in find_best_drop: {str(e)}")
        return None

def analyze_audio(youtube_link):
    """Main analysis function with improved error handling and silence removal."""
    try:
        audio_file = download_audio(youtube_link)
        if not audio_file:
            log_error("Failed to download audio")
            return None

        wav_file = convert_audio_to_wav(audio_file)
        if not wav_file:
            log_error("Failed to convert audio to WAV")
            os.remove(audio_file)
            return None

        y, sr = librosa.load(wav_file, sr=None)
        duration = librosa.get_duration(y=y, sr=sr)

        # Remove silence using librosa.effects.split()
        intervals = librosa.effects.split(y, top_db=20)
        non_silent_y = np.concatenate([y[start:end] for start, end in intervals])

        diff_curve, hop_length = detect_drops(non_silent_y, sr)
        if diff_curve is None:
            log_error("Failed to detect drops")
            os.remove(audio_file)
            os.remove(wav_file)
            return None

        timestamps = find_best_drop(diff_curve, hop_length, sr, duration)

        # Cleanup
        os.remove(audio_file)
        os.remove(wav_file)

        if timestamps is None:
            log_error("No suitable segments found")
            return None

        return timestamps

    except Exception as e:
        log_error(f"Error in analyze_audio: {str(e)}")
        return None
