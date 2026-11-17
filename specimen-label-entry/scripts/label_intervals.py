"""Keep readable intervals out of scalar fields and out of correction donors."""
import re
import unicodedata

FIELDS={'海拔','经度','纬度','采集日期','鉴定日期'}
NUMBER=r'[+-]?\d+(?:\.\d+)?'
SEPARATOR=r'\s*(?:至|到|[-–—~～])\s*'
ALTITUDE=re.compile(r'(?<![\d.])'+NUMBER+r'\s*(?:m|米)?'+SEPARATOR+NUMBER+r'\s*(?:m(?![A-Za-z])|米)',re.I)
DATE=r'20\d{2}[-./]\d{1,2}[-./]\d{1,2}'
DATE_END=r'(?:'+DATE+r'|\d{1,2}[-./]\d{1,2})'
DATES=re.compile(r'(?<!\d)'+DATE+SEPARATOR+DATE_END+r'(?!\d)')
NUMBERS=re.compile(r'^\s*'+NUMBER+r'\s*(?:m|米)?'+SEPARATOR+NUMBER+r'\s*(?:m|米)?\s*$',re.I)


def normalized(text):
    return unicodedata.normalize('NFKC',text)


def source_fragment(raw,match):
    pieces=[normalized(char) for char in raw]
    if ''.join(pieces)!=normalized(raw):return raw
    offsets=[index for index,piece in enumerate(pieces) for _ in piece]
    return raw[offsets[match.start()]:offsets[match.end()-1]+1]


def observations(record):
    supplied=record.get('interval_observations',{})
    if not isinstance(supplied,dict) or set(supplied)-FIELDS:
        raise ValueError('Interval observations must use scalar altitude, coordinate or date fields')
    result={}
    for field,value in supplied.items():
        if not isinstance(value,dict) or not isinstance(value.get('raw_text'),str) or not value['raw_text'].strip():
            raise ValueError('Each interval observation needs the unchanged raw_text')
        result[field]=dict(value)
    for field in sorted(FIELDS):
        role='identification' if field=='鉴定日期' else 'collection'
        raw=record.get(role+'_label_text')
        pattern=DATES if '日期' in field else ALTITUDE if field=='海拔' else None
        if isinstance(raw,str) and pattern:
            match=pattern.search(normalized(raw))
            if match:
                result.setdefault(field,{'raw_text':source_fragment(raw,match),
                    'normalized_interval':match.group(0),'basis':role+'_label_text'})
        for container in ['observed_values','values']:
            value=record.get(container,{}).get(field)
            if isinstance(value,str):
                pattern=DATES if '日期' in field else NUMBERS
                match=pattern.search(normalized(value))
                if match:
                    result.setdefault(field,{'raw_text':value,'basis':container})
    return result
