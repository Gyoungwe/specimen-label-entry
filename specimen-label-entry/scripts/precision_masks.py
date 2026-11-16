"""Validate reviewed image-space masks and clean background outside protected pixels."""
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def polygon_mask(size, polygons):
    mask = Image.new('L', size, 0)
    draw = ImageDraw.Draw(mask)
    for polygon in polygons:
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise ValueError('Each polygon needs at least three points')
        points = []
        for point in polygon:
            if (not isinstance(point, list) or len(point) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point)):
                raise ValueError('Polygon points must be finite [x,y] image pixels')
            x, y = point
            if not (0 <= x < size[0] and 0 <= y < size[1]):
                raise ValueError('Polygon lies outside its declared crop; use native crop pixels')
            pixel = (round(x), round(y))
            if not (0 <= pixel[0] < size[0] and 0 <= pixel[1] < size[1]):
                raise ValueError('Rounded polygon point lies outside the native crop')
            points.append(pixel)
        draw.polygon(points, fill=255)
    return mask


def validate_mask_plan(plan, image, sha256):
    if not isinstance(plan, dict) or plan.get('reviewed') is not True:
        raise ValueError('Inspect the crop and review the precision mask plan')
    if plan.get('coordinate_space') != 'crop_pixels' or plan.get('image_size') != list(image.size):
        raise ValueError('Precision masks require native crop_pixels and the exact image_size')
    if plan.get('source_sha256') != sha256:
        raise ValueError('Precision mask source SHA-256 differs')
    masks = {}
    for name in ['include_polygons', 'exclude_polygons', 'protected_polygons']:
        polygons = plan.get(name, [])
        if not isinstance(polygons, list):
            raise ValueError(f'{name} must be a list of polygons')
        masks[name] = polygon_mask(image.size, polygons)
    return masks


def paper_background(image, samples):
    """Interpolate reviewed paper colours and reuse their native texture.

    Bounded distance weights avoid unstable plane extrapolation when sample
    patches are almost collinear (for example along a crop's bottom edge).
    """
    if not isinstance(samples, list) or not samples:
        raise ValueError('Select at least one inspected blank background sample box')
    pixels = np.asarray(image.convert('RGBA'), dtype=np.float64)
    positions, colours, patches = [], [], []
    for box in samples:
        if (not isinstance(box, list) or len(box) != 4 or
            any(isinstance(v, bool) or not isinstance(v, int) for v in box)):
            raise ValueError('Background samples must be integer [left,top,right,bottom] boxes')
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
            raise ValueError('Background sample lies outside the source crop')
        patch = pixels[y0:y1, x0:x1]
        if patch.shape[0] * patch.shape[1] < 16:
            raise ValueError('Background sample is too small')
        colour = np.median(patch.reshape(-1, 4), axis=0)
        contrast = np.mean(np.abs(patch[:, :, :3] - colour[:3]), axis=2)
        if np.mean(contrast > 35) > .08:
            raise ValueError('Background sample contains strong foreground; choose blank paper')
        positions.append([(x0+x1)/2/image.width, (y0+y1)/2/image.height, 1])
        colours.append(colour)
        patches.append(patch-colour)
    positions = np.asarray(positions)
    colours = np.asarray(colours)
    ys, xs = np.mgrid[:image.height, :image.width]
    xs=xs/image.width;ys=ys/image.height
    weights=np.stack([1/np.maximum((xs-x)**2+(ys-y)**2,.0025)**2 for x,y,_ in positions])
    weights/=weights.sum(axis=0)
    estimated=np.einsum('nhw,nc->hwc',weights,colours)
    texture=np.zeros_like(estimated)
    rng=np.random.default_rng(314159)
    for weight,patch in zip(weights,patches):
        tiled=np.tile(patch,(math.ceil(image.height/patch.shape[0])+1,math.ceil(image.width/patch.shape[1])+1,1))
        dy=int(rng.integers(0,patch.shape[0]));dx=int(rng.integers(0,patch.shape[1]))
        texture+=tiled[dy:dy+image.height,dx:dx+image.width]*weight[:,:,None]
    # Retain the sampled texture's contrast when several patches overlap.
    estimated+=texture/np.sqrt(np.sum(weights**2,axis=0))[:,:,None]
    return np.clip(estimated, 0, 255)


def clean_background(image, plan, sha256):
    if image.mode not in ('RGB', 'RGBA', 'L'):
        raise ValueError('Clean a lossless RGB, RGBA or grayscale crop; retain the original source')
    masks = validate_mask_plan(plan, image, sha256)
    if plan.get('include_polygons'):
        raise ValueError('include_polygons are ruler-only; clean crops with exclusion and protected polygons')
    if not plan.get('exclude_polygons') or not plan.get('protected_polygons'):
        raise ValueError('Background cleanup needs explicit exclusion and protected subject polygons')
    margin = plan.get('protection_margin', 3)
    feather = plan.get('feather_radius', 2)
    if (isinstance(margin, bool) or not isinstance(margin, int) or not 0 <= margin <= 30 or
        isinstance(feather, bool) or not isinstance(feather, int) or not 0 <= feather <= 10):
        raise ValueError('Use protection_margin 0..30 and feather_radius 0..10 in source pixels')
    protected = masks['protected_polygons']
    if margin:
        protected = protected.filter(ImageFilter.MaxFilter(2*margin+1))
    exclusion = masks['exclude_polygons']
    expanded = exclusion.filter(ImageFilter.MaxFilter(2*feather+1)) if feather else exclusion
    softened = expanded.filter(ImageFilter.GaussianBlur(feather)) if feather else expanded
    strength = np.maximum(np.asarray(exclusion), np.asarray(softened)).astype(np.float64)/255
    keep = np.asarray(protected) > 0
    strength[keep] = 0
    if not np.any(strength):
        raise ValueError('Exclusion is completely protected; refine the mask')
    background = paper_background(image, plan.get('background_samples'))
    original = np.asarray(image.convert('RGBA'), dtype=np.float64)
    result = np.uint8(np.clip(np.rint(original*(1-strength[:, :, None]) + background*strength[:, :, None]), 0, 255))
    output = Image.fromarray(result).convert(image.mode)
    before, after = np.asarray(image), np.asarray(output)
    if np.any(before[keep] != after[keep]) or np.any(before[strength == 0] != after[strength == 0]):
        raise ValueError('Protected subject or pixels outside cleanup mask changed')
    report = {'changed_pixels': int(np.count_nonzero(np.any(before != after, axis=2) if before.ndim == 3 else before != after)),
              'protected_pixels': int(np.count_nonzero(keep)), 'protected_pixels_unchanged': True,
              'outside_mask_unchanged': True, 'size_unchanged': output.size == image.size,
              'method': 'reviewed-source paper colour interpolation and native texture',
              'use': 'cleaned_display_copy; retain original evidence crop'}
    return output, report
