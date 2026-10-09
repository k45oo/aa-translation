"""AA 图片版 → CBZ 漫画包(整部一包,章节前缀定宽排序)
用法: python scripts/tools/aa_to_cbz.py --work saki [--out xxx.cbz] [--per-chapter]"""
import sys, os, re, zipfile, argparse
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
import registry

EID_RE = re.compile(r"(\d+)")
IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')

def chapter_dirs(img_root):
    """章目录按 eid 数字排序(与汉化文件顺序一致)"""
    dirs = [d for d in os.listdir(img_root) if os.path.isdir(os.path.join(img_root, d))]
    def key(d):
        m = EID_RE.search(d)
        return int(m.group(1)) if m else 99999
    return sorted(dirs, key=key)

FULL2HALF = str.maketrans('０１２３４５６７８９', '0123456789')

def clean_label(s):
    """zip 路径安全: 非法字符转全角"""
    return re.sub(r'[\\/:*?"<>|]', '／', s)

def chapter_label(chdir, tr_dir=None):
    """章标签: 优先读汉化 txt 标题(FC2 行尾全角话数→第N话,番外用原标题);
    无 txt 时从目录名提取 第N話"""
    if tr_dir:
        p = os.path.join(tr_dir, chdir + '_汉化.txt')
        if os.path.exists(p):
            first = open(p, encoding='utf-8').readline().strip().translate(FULL2HALF)
            m = re.search(r'第(\d+)[話话]', first)
            if m:
                return f'第{m.group(1)}话'
            m = re.search(r'[　\s](\d{1,2})[　\s]*(【完】)?$', first)
            if m:
                return f'第{m.group(1)}话' + (m.group(2) or '')
            t = re.sub(r'^#\s*', '', first).strip()
            if t:
                return clean_label(t)
    m = re.search(r'(第\d+[話话])', chdir)
    return m.group(1) if m else chdir

def add_dir(zf, prefix, chdir):
    """章目录 → zip: prefix 为子目录名(空=平铺),页序定宽命名(所有图片格式,STORED)"""
    files = sorted(f for f in os.listdir(chdir) if f.lower().endswith(IMG_EXTS))
    for i, f in enumerate(files, 1):
        ext = os.path.splitext(f)[1].lower()
        arc = f'{i:04d}{ext}'
        if prefix:
            arc = f'{prefix}/{arc}'
        zf.write(os.path.join(chdir, f), arc, compress_type=zipfile.ZIP_STORED)
    return len(files)

def main():
    ap = argparse.ArgumentParser(description='图片目录 → CBZ 漫画包(章节子目录)')
    ap.add_argument('--work', default='', help='registry 作品 id(图片版目录 aaManga/交付HTML/<work>_图片版)')
    ap.add_argument('--dir', default='', help='直接打包此目录下的子目录(任意图片漫画目录,如 comic_seamless)')
    ap.add_argument('--out', default='')
    ap.add_argument('--per-chapter', action='store_true', help='每章一个 CBZ')
    ap.add_argument('--prefix', default='', help='--dir 模式每话文件名前缀(如 saki_fc2_ → saki_fc2_001_第1话.cbz)')
    ap.add_argument('--tr-dir', default='', help='--dir 模式章节标签数据源(汉化txt目录)')
    a = ap.parse_args()
    if a.dir:
        img_root = a.dir
        tr_dir = a.tr_dir
        out_base = os.path.splitext(os.path.basename(img_root))[0]
    elif a.work:
        cfg = registry.get(a.work)
        img_root = os.path.join(registry.BASE, 'aaManga', '交付HTML', a.work + '_图片版')
        tr_dir = cfg.get('out_dir', '')
        out_base = a.work
    else:
        raise SystemExit('需 --work 或 --dir')
    if not os.path.isdir(img_root):
        raise SystemExit('图片目录不存在: ' + img_root)
    chs = chapter_dirs(img_root)
    print(f'{len(chs)} 章 → {img_root}')

    if a.per_chapter:
        for ci, ch in enumerate(chs, 1):
            label = chapter_label(ch, tr_dir)
            if a.dir:
                fname = f'{a.prefix}{ci:03d}_{label}.cbz' if a.prefix else f'{ch}.cbz'
            else:
                fname = f'{out_base}_{ci:03d}_{label}.cbz'
            if a.out:
                os.makedirs(a.out, exist_ok=True)
                out = os.path.join(a.out, fname)
            else:
                out = os.path.join(registry.BASE, 'aaManga', '交付HTML', fname)
            with zipfile.ZipFile(out, 'w') as zf:
                add_dir(zf, '', os.path.join(img_root, ch))
            with zipfile.ZipFile(out) as zf2:
                n = len(zf2.namelist())
            print(f'{out}: {n} 页')
    else:
        out = a.out or os.path.join(registry.BASE, 'aaManga', '交付HTML', f'{out_base}_图片版.cbz')
        with zipfile.ZipFile(out, 'w') as zf:
            for ci, ch in enumerate(chs, 1):
                add_dir(zf, f'{ci:03d}_{chapter_label(ch, tr_dir)}', os.path.join(img_root, ch))
        with zipfile.ZipFile(out) as zf2:
            names = zf2.namelist()
        n = len(names)
        size = os.path.getsize(out) / 1048576
        print(f'{out}: {n} 页, {size:.1f} MB')
        dirs = sorted({n.split('/')[0] for n in names})
        print(f'章节目录: {dirs[:5]}{"..." if len(dirs) > 5 else ""} (共 {len(dirs)} 章)')

if __name__ == '__main__':
    main()
