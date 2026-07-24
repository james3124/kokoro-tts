import os, gc
os.environ["HF_HOME"] = "./hf_cache"
import torch
torch.set_num_threads(1)
from kokoro import KPipeline
p = KPipeline(lang_code="a")
for m in p.model.modules():
    if hasattr(m, 'weight') and m.weight is not None:
        m.weight.data = m.weight.data.half()
gc.collect()
print("Done!")
