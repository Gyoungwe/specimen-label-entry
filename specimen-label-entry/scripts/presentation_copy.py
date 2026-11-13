#!/usr/bin/env python3
"""Optional conservative white-balance/cutout copies; never entry evidence."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
from photo_groups import digest, verify


def presentation(manifest_path,out,cutout=False):
    if out.exists():
        raise ValueError('Choose a new presentation directory')
    verify(manifest_path)
    manifest=json.loads(manifest_path.read_text())
    out.mkdir(parents=True)
    results=[]
    for group in manifest['groups']:
        source=Path(group['components']['specimen']['path'])
        before=digest(source)
        with Image.open(source) as original:
            pixels=np.asarray(original.convert('RGB'),dtype=np.float32)
        border=np.concatenate((pixels[0],pixels[-1],pixels[:,0],pixels[:,-1]),axis=0)
        background=np.median(border,axis=0)
        target=float(np.mean(background))
        gains=np.clip(target/np.maximum(background,1),0.85,1.18)
        corrected=np.uint8(np.clip(pixels*gains,0,255))
        colour_path=out/(group['specimen_number']+'__white-balance-estimate.png')
        Image.fromarray(corrected).save(colour_path)
        entry={'specimen_number':group['specimen_number'],'source':str(source),
               'source_sha256':before,'estimated_background_rgb':background.tolist(),
               'gains':gains.tolist(),'colour_copy':str(colour_path.resolve())}
        if cutout:
            # Soft mask preserves weak edges; no redrawing, erasing or shape completion.
            distance=np.sqrt(np.mean((pixels-background)**2,axis=2))
            alpha=np.uint8(np.clip((distance-9)/17,0,1)*255)
            rgba=np.dstack((corrected,alpha))
            cutout_path=out/(group['specimen_number']+'__cutout-draft.png')
            Image.fromarray(rgba).save(cutout_path)
            entry['cutout_draft']=str(cutout_path.resolve())
        if digest(source)!=before:
            raise ValueError('Evidence crop changed')
        results.append(entry)
    report={'use':'presentation_only','colour_reference':'estimated paper background; no colour card',
            'mask':'soft colour-distance draft; inspect antennae, legs and translucent wings',
            'human_review_pending':True,'groups':results}
    (out/'presentation-processing.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'presentation_copies':len(results),'cutout_drafts':cutout,'out':str(out)},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--cutout',action='store_true')
    args=parser.parse_args()
    presentation(args.manifest,args.out,args.cutout)
