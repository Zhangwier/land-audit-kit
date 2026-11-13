"""Build escaped local evidence HTML from current explicit entries, without copying evidence."""
import argparse,hashlib,html,json,os
from pathlib import Path
from PIL import Image

def build(root,entries,output):
 root=Path(root).resolve();output=Path(output).resolve()
 if output.exists() or output.with_suffix('.manifest.json').exists():raise FileExistsError('索引或清单已存在')
 if not isinstance(entries,list) or not entries:raise ValueError('图证条目须为非空数组')
 esc=lambda x:html.escape(str(x),quote=True)
 parts=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>本轮图证索引</title><style>body{max-width:1100px;margin:30px auto;padding:18px;font:16px/1.7 sans-serif}img{max-width:100%;height:auto}details{border:1px solid #ccc;margin:16px 0;padding:14px}summary{cursor:pointer}p{white-space:pre-wrap}</style><h1>本轮图证索引</h1><p>图证仅定位观察，不等于事实、几何或业务审定。</p>'];manifest=[]
 for e in entries:
  if not e.get('title') or not e.get('images'):raise ValueError('每条须有标题和图片')
  parts.append('<details><summary>'+esc(e['title'])+'</summary><p>'+esc(e.get('note',''))+'</p>')
  for rel in e['images']:
   image=(root/rel).resolve()
   if root not in image.parents or image.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:raise ValueError('图片越界或类型不支持')
   if image==output:raise ValueError('不能覆盖图证')
   with Image.open(image) as im:im.verify()
   with image.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
   try:uri=Path(os.path.relpath(image,output.parent)).as_posix();from urllib.parse import quote;uri=quote(uri,safe='/')
   except ValueError:uri=image.as_uri()
   parts.append('<figure><a href="'+esc(uri)+'"><img loading="lazy" src="'+esc(uri)+'"></a><figcaption>'+esc(rel)+'</figcaption></figure>');manifest.append(dict(relative_path=str(rel),sha256=sha))
  parts.append('</details>')
 parts.append('</html>')
 output.write_text('\n'.join(parts),encoding='utf8');output.with_suffix('.manifest.json').write_text(json.dumps(dict(images=manifest,note='未复制图片；索引依赖当前图证相对位置，搬迁后需核链接与哈希'),ensure_ascii=False,indent=2),encoding='utf8')
 return manifest
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('entries');p.add_argument('--root',required=True);p.add_argument('--output',required=True);a=p.parse_args()
 try:
  if Path(a.output).resolve()==Path(a.entries).resolve() or Path(a.output).with_suffix('.manifest.json').resolve()==Path(a.entries).resolve():raise ValueError('不能覆盖输入清单')
  build(a.root,json.loads(Path(a.entries).read_text(encoding='utf-8-sig')),a.output);print('已生成图证索引与哈希清单，未复制原图');return 0
 except (ValueError,OSError,TypeError,KeyError) as e:print(str(e));return 1
if __name__=='__main__':raise SystemExit(main())
