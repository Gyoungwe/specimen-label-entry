"""Extract photographed ruler ink and place it without covering specimen evidence."""
from collections import deque
import numpy as np
from PIL import Image,ImageFilter


def extract_ink(scale):
    rgba=scale.convert('RGBA')
    gray=np.asarray(scale.convert('L'),dtype=np.float32)
    visible=np.asarray(rgba.getchannel('A'))>0
    if not np.any(visible):raise ValueError('Scale crop is transparent')
    background=float(np.percentile(gray[visible],85))
    alpha=np.uint8(np.clip((background-gray-30)/65,0,1)*255)
    alpha=np.minimum(alpha,np.asarray(rgba.getchannel('A')))
    # Keep soft edges connected to dark ink. Isolated paper texture and pale fibres drop out.
    support=bytearray((alpha>8).tobytes())
    reached=bytearray(alpha.size)
    seeds=np.flatnonzero(alpha.reshape(-1)>=170).tolist()
    queue=deque(seeds)
    for index in seeds:reached[index]=1
    width,height=rgba.size
    while queue:
        index=queue.popleft();x=index%width;y=index//width
        neighbours=[]
        if x:neighbours.append(index-1)
        if x+1<width:neighbours.append(index+1)
        if y:neighbours.append(index-width)
        if y+1<height:neighbours.append(index+width)
        for other in neighbours:
            if support[other] and not reached[other]:
                reached[other]=1;queue.append(other)
    alpha=np.where(np.frombuffer(reached,dtype=np.uint8).reshape(gray.shape),alpha,0).astype(np.uint8)
    mask=Image.fromarray(alpha)
    bounds=mask.getbbox()
    if bounds is None or np.count_nonzero(alpha)<20 or np.count_nonzero(alpha)>alpha.size*0.65:
        raise ValueError('Scale ink cannot be isolated; inspect or recrop the source ruler')
    rgba.putalpha(mask)
    asset=rgba.crop(bounds)
    return asset,{'method':'paper-luminance alpha with dark-connected soft edges',
        'background_luma':background,'source_size':list(scale.size),'ink_box':list(bounds),
        'asset_size':list(asset.size),'scale_factor':1,'human_review_pending':True}


def protected_boxes(group,target,size,margin=8):
    if target=='specimen':
        return [[0,0,size[0],size[1]]]
    left,top,_,_=group['group_box']
    boxes=[]
    for role,component in group['components'].items():
        if component is None:continue
        x0,y0,x1,y1=component['box']
        boxes.append([max(0,x0-left-margin),max(0,y0-top-margin),
                      min(size[0],x1-left+margin),min(size[1],y1-top+margin)])
    return boxes


def overlaps(a,b):
    return a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]


def quiet_position(base,asset,forbidden,margin=12):
    width,height=base.size;aw,ah=asset.size
    if aw+2*margin>width or ah+2*margin>height:return None
    # Integral image makes candidate contrast checks independent of ruler size.
    gray=np.asarray(base.convert('L'),dtype=np.float64)
    samples=gray[::max(1,height//300),::max(1,width//300)]
    light=float(np.percentile(samples,75))
    dark=(gray<max(25,min(light-65,110))).astype(np.int64)
    integral=np.pad(dark.cumsum(0).cumsum(1),((1,0),(1,0)))
    xs=set(range(margin,width-aw-margin+1,max(16,aw//5)))
    ys=set(range(margin,height-ah-margin+1,max(16,ah//4)))
    xs.add(width-aw-margin);ys.add(height-ah-margin)
    best=None
    for y in sorted(ys):
        for x in sorted(xs):
            box=[x,y,x+aw,y+ah]
            if any(overlaps(box,b) for b in forbidden):continue
            count=integral[y+ah,x+aw]-integral[y,x+aw]-integral[y+ah,x]+integral[y,x]
            if count/(aw*ah)>0.008:continue
            # Prefer a quiet lower corner, with no change to existing content.
            score=(count/(aw*ah),height-ah-y,width-aw-x)
            if best is None or score<best[0]:best=(score,box)
    return best[1] if best else None


def extend_background(base,asset,margin=32):
    width=max(base.width,asset.width+2*margin)
    height=base.height+asset.height+2*margin
    # Estimate the paper from bright edge pixels, discarding dark label lines.
    # The original image is pasted back byte-for-byte after background construction.
    rgba=np.asarray(base.convert('RGBA'))
    strip=rgba[max(0,base.height-96):]
    base_x=(width-base.width)//2
    edge=np.percentile(strip,75,axis=0).astype(np.uint8)
    edge=np.pad(edge,((base_x,width-base.width-base_x),(0,0)),mode='edge')
    line=Image.fromarray(edge[None,:,:]).convert('RGB').filter(ImageFilter.GaussianBlur(24))
    target=np.asarray(line,dtype=np.float32)
    start=np.median(rgba[-min(8,base.height):,:,:3],axis=0).astype(np.uint8)
    start=np.pad(start,((base_x,width-base.width-base_x),(0,0)),mode='edge')
    start=np.asarray(Image.fromarray(start[None,:,:]).filter(ImageFilter.GaussianBlur(4)),dtype=np.float32)
    start=np.where(start.mean(2,keepdims=True)<110,target,start)
    fraction=np.clip((np.arange(height,dtype=np.float32)-base.height)/max(1,height-base.height-1),0,1)[:,None,None]
    background=start*(1-fraction)+target*fraction
    # Small deterministic paper grain avoids a flat synthetic-looking block.
    rng=np.random.default_rng(271828)
    background=np.uint8(np.clip(background+rng.normal(0,0.9,(height,width,1)),0,255))
    canvas=Image.fromarray(background).convert(base.mode)
    canvas.paste(base,(base_x,0))
    return (canvas,[base_x,0,base_x+base.width,base.height],
            [width-margin-asset.width,base.height+margin,width-margin,base.height+margin+asset.height])


def apply_asset(base,asset,box):
    result=base.copy()
    region=base.crop(box).convert('RGBA')
    rendered=Image.alpha_composite(region,asset).convert(base.mode)
    result.paste(rendered,(box[0],box[1]))
    return result


def compose_overlay(base,asset,group,target,shared):
    outer=group['group_box'];box=shared['box']
    if target=='group' and outer[0]<=box[0] and outer[1]<=box[1] and box[2]<=outer[2] and box[3]<=outer[3]:
        local=[box[0]-outer[0],box[1]-outer[1],box[2]-outer[0],box[3]-outer[1]]
        return base.copy(),[0,0,base.width,base.height],local,'original_scale_visible'
    forbidden=protected_boxes(group,target,base.size)
    placement=quiet_position(base,asset,forbidden)
    if placement is not None:
        canvas=apply_asset(base,asset,placement)
        return canvas,[0,0,base.width,base.height],placement,'transparent_overlay'
    canvas,base_box,placement=extend_background(base,asset)
    return apply_asset(canvas,asset,placement),base_box,placement,'matched_background_extension'


def protected_unchanged(original,composed,group,target,base_box):
    shift_x,shift_y=base_box[:2]
    for box in protected_boxes(group,target,original.size,margin=0):
        target_box=[box[0]+shift_x,box[1]+shift_y,box[2]+shift_x,box[3]+shift_y]
        if original.crop(box).tobytes()!=composed.crop(target_box).tobytes():return False
    return True
