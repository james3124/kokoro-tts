import os
os.environ["HF_HOME"] = "./hf_cache"

print("Downloading Kokoro-82M weights...")
from kokoro import KPipeline
p = KPipeline(lang_code="a")
print("Model cached!")
