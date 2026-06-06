# === OpenRA Qwen-0.5B v7 (9-intent+air+tanya command surface) — train + eval in Colab ===
import subprocess, sys, os, json, re
def pip(a): subprocess.check_call([sys.executable,'-m','pip','install','-q']+a.split())
pip('unsloth'); pip('--no-deps trl peft accelerate bitsandbytes'); pip('datasets')

import torch
print('gpu', torch.cuda.get_device_name(0), '| cuda', torch.cuda.is_available())

BASE='https://raw.githubusercontent.com/jiziyi-hue/openramcp/main/data'
subprocess.check_call(['wget','-q','-O','sft_train.jsonl',BASE+'/sft_train.jsonl'])
subprocess.check_call(['wget','-q','-O','sft_val.jsonl',BASE+'/sft_val.jsonl'])
print('data:', os.path.getsize('sft_train.jsonl'), os.path.getsize('sft_val.jsonl'), 'bytes')

from unsloth import FastLanguageModel
MAX=1024
model,tok=FastLanguageModel.from_pretrained('unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit',
    max_seq_length=MAX,dtype=None,load_in_4bit=True)
model=FastLanguageModel.get_peft_model(model,r=16,lora_alpha=32,lora_dropout=0,bias='none',
    target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'],
    use_gradient_checkpointing='unsloth',random_state=42)

from datasets import load_dataset
ds=load_dataset('json',data_files={'train':'sft_train.jsonl','val':'sft_val.jsonl'})
def fmt(e): return {'text':tok.apply_chat_template(e['messages'],tokenize=False,add_generation_prompt=False)}
ds=ds.map(fmt,remove_columns=ds['train'].column_names)
print('train',len(ds['train']),'val',len(ds['val']))

from trl import SFTTrainer,SFTConfig
cfg=SFTConfig(output_dir='v4',per_device_train_batch_size=8,gradient_accumulation_steps=2,
    num_train_epochs=3,learning_rate=2e-4,warmup_ratio=0.05,lr_scheduler_type='cosine',
    logging_steps=20,save_strategy='no',eval_strategy='epoch',max_length=MAX,
    fp16=True,optim='adamw_8bit',report_to='none',seed=42)
tr=SFTTrainer(model=model,tokenizer=tok,train_dataset=ds['train'],eval_dataset=ds['val'],
    args=cfg,dataset_text_field='text')
res=tr.train()
print('=== final loss %.4f ==='%res.training_loss)

# --- in-Colab eval on val (so we can read metrics via screenshot) ---
FastLanguageModel.for_inference(model)
val=[json.loads(l) for l in open('sft_val.jsonl',encoding='utf-8') if l.strip()]
def parse(t):
    t=t.strip()
    if t.startswith('```'): t='\n'.join(t.split('\n')[1:]).rsplit('```',1)[0]
    try: return json.loads(t)
    except: pass
    a,b=t.find('{'),t.rfind('}')
    if 0<=a<b:
        try: return json.loads(t[a:b+1])
        except: return None
    return None
def kind(o): return (o.get('intent') or '?') if isinstance(o,dict) else '_bad'
pn=ia=tg=ex=0
for e in val:
    gt=json.loads(e['messages'][-1]['content']); pm=e['messages'][:-1]
    pr=tok.apply_chat_template(pm,tokenize=False,add_generation_prompt=True)
    inp=tok(pr,return_tensors='pt').to(model.device)
    with torch.no_grad(): out=model.generate(**inp,max_new_tokens=128,do_sample=False,pad_token_id=tok.eos_token_id)
    p=parse(tok.decode(out[0][inp.input_ids.shape[1]:],skip_special_tokens=True))
    if p is not None:
        pn+=1
        if kind(p)==kind(gt): ia+=1
        gtt=gt.get('target',{}).get('name'); pt=p.get('target',{}).get('name') if isinstance(p,dict) else None
        if gtt and gtt==pt: tg+=1
        if json.dumps(p,sort_keys=True,ensure_ascii=False)==json.dumps(gt,sort_keys=True,ensure_ascii=False): ex+=1
n=len(val)
print('==== V7 EVAL n=%d ===='%n)
print('parse   %.1f%%'%(100*pn/n))
print('intent  %.1f%%'%(100*ia/n))
print('target  %.1f%%'%(100*tg/n))
print('exact   %.1f%%'%(100*ex/n))
print('==== END ====')

# --- save + ship out (both files.download AND tmpfiles backup) ---
model.save_pretrained('qwen05b_openra_lora_v7'); tok.save_pretrained('qwen05b_openra_lora_v7')
import shutil
shutil.make_archive('qwen05b_openra_lora_v7','zip','qwen05b_openra_lora_v7')
try:
    r=subprocess.check_output(['curl','-sfL','-F','file=@qwen05b_openra_lora_v7.zip','https://tmpfiles.org/api/v1/upload'],timeout=180).decode()
    print('TMPFILES_URL:', json.loads(r)['data']['url'])
except Exception as e: print('tmpfiles failed:', str(e)[:60])
from google.colab import files
files.download('qwen05b_openra_lora_v7.zip')
print('ALL DONE')
