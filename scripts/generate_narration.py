#!/usr/bin/env python3
"""Generate the Introduction tour's voice-over MP3s with Amazon Polly and
upload them to the site bucket under /intro-audio/.

Usage:  python3 scripts/generate_narration.py <site-bucket> [--dry-run]

Voices (verified in us-east-1):
  en  Matthew  generative     zh  Zhiyu   neural     yue Hiujin  neural
  ja  Takumi   neural         ko  Seoyeon generative

Also emits frontend/intro-captions.json (scene text per language) so the
player can render synchronized captions without a second source of truth.
"""

import json
import os
import sys

import boto3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NARRATION = json.load(open(os.path.join(ROOT, "scripts", "narration.json")))
SCENES = NARRATION["scenes"]
LANGS = [k for k in NARRATION if k not in ("scenes",)]

polly = boto3.client("polly", region_name="us-east-1")
s3 = boto3.client("s3")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: generate_narration.py <site-bucket> [--dry-run]")
    bucket = sys.argv[1]
    dry = "--dry-run" in sys.argv

    captions = {}
    total = 0
    for lang in LANGS:
        cfg = NARRATION[lang]
        captions[lang] = {"name": cfg["name"], "scenes": {}}
        for scene in SCENES:
            text = cfg[scene]
            captions[lang]["scenes"][scene] = text
            key = f"intro-audio/{lang}/{scene}.mp3"
            if dry:
                print(f"would synth {key} ({len(text)} chars, {cfg['voice']}/{cfg['engine']})")
                continue
            resp = polly.synthesize_speech(
                Text=text,
                OutputFormat="mp3",
                VoiceId=cfg["voice"],
                Engine=cfg["engine"],
                LanguageCode={"en": "en-US", "zh": "cmn-CN", "yue": "yue-CN",
                              "ja": "ja-JP", "ko": "ko-KR"}[lang],
            )
            body = resp["AudioStream"].read()
            s3.put_object(Bucket=bucket, Key=key, Body=body,
                          ContentType="audio/mpeg",
                          CacheControl="public, max-age=86400")
            total += len(body)
            print(f"  {key}: {len(body)//1024} KB")

    cap_path = os.path.join(ROOT, "frontend", "intro-captions.json")
    json.dump(captions, open(cap_path, "w"), ensure_ascii=False, indent=1)
    print(f"captions -> {cap_path}")
    if not dry:
        print(f"uploaded {total//1024} KB of audio to s3://{bucket}/intro-audio/")


if __name__ == "__main__":
    main()
