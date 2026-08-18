import os,re
root='.'
pats=re.compile(r'ChapterChat|chapter_writer|chapter_rewriter|stream_chapter|ContentWriter|llm_client|writing_outline|writing_objectives|repair_messages|draft_messages|research|outline',re.I)
ex={'.py','.ts','.tsx','.js','.vue'}
for dp,ds,fs in os.walk(root):
    if '.git' in dp or 'node_modules' in dp: continue
    for f in fs:
        if os.path.splitext(f)[1] not in ex: continue
        p=os.path.join(dp,f)
        try: lines=open(p,encoding='utf-8',errors='ignore').read().splitlines()
        except: continue
        for i,line in enumerate(lines,1):
            if pats.search(line): print(f'{p}:{i}:{line[:240]}')
