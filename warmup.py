import os, gc
os.environ["HF_HOME"] = "./hf_cache"

import torch
torch.set_num_threads(1)

print("Downloading Kokoro-82M (float16 to save RAM)...")
from kokoro import KPipeline

p = KPipeline(lang_code="a")

# Convert model weights to float16 — halves VRAM/RAM from ~330MB to ~165MB
for module in p.model.modules():
    if hasattr(module, 'weight') and module.weight is not None:
        module.weight.data = module.weight.data.half()

gc.collect()
print("✅ Model cached in float16!")
