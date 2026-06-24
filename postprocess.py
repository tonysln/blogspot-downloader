#!/usr/bin/env python3

import argparse
import datetime
import glob
import html
import os
import re
import sys

from bs4 import BeautifulSoup

# reuse the downloader's pure url helpers (importing only runs setrlimit; the
# arg parsing lives under its __main__ guard, so this has no side effects).
from blogspot_downloader import looks_like_image_url, upgrade_blogspot_image_url

# post subfolder name produced by the downloader: YYYY-MM-DD_HHMMSS_slug
POST_DIR_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})_(.*)$')


def iter_image_candidates(soup):
    """Replay save_post_images' iteration over <img> tags.

    Yields (img_tag, candidate_url, idx) in document order. candidate_url is the
    deduped, size-upgraded url the downloader would have fetched; idx is its
    1-based saved-file number. Repeated candidates reuse the first idx (the
    downloader only saved them once) and so still get rewritten to the local
    file.
    """
    seen = {}
    idx = 0
    for img in soup.find_all('img'):
        src = img.get('src')
        if not src:
            continue
        candidate = src
        link = img.find_parent('a')
        if link and link.get('href') and looks_like_image_url(link.get('href')):
            candidate = link.get('href')
        candidate = upgrade_blogspot_image_url(candidate)
        if candidate in seen:
            yield img, candidate, seen[candidate]
            continue
        idx += 1
        seen[candidate] = idx
        yield img, candidate, idx


def local_file_for(idx, post_dir):
    """Return the saved image filename for the idx-th image, or None.

    Matches by the NN_ prefix so it is robust to download_image's extension
    guessing / html-wrapper renames. Skips home.html and the post pdf.
    """
    matches = sorted(glob.glob(os.path.join(post_dir, '{:02d}_*'.format(idx))))
    for path in matches:
        if os.path.isfile(path):
            return os.path.basename(path)
    return None


def localize_post(post_dir, dry_run):
    """Rewrite index.html in post_dir to reference local image files.

    Returns the number of <img> tags repointed to local files.
    """
    index_path = os.path.join(post_dir, 'index.html')
    with open(index_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f.read(), 'lxml')

    local_for_idx = {}
    changed = 0
    for img, candidate, idx in iter_image_candidates(soup):
        if idx not in local_for_idx:
            local_for_idx[idx] = local_file_for(idx, post_dir)
        local = local_for_idx[idx]
        if not local:
            continue  # download failed/skipped: leave this image remote
        if img.get('src') != local:
            img['src'] = local
            changed += 1
        link = img.find_parent('a')
        if link and link.get('href') and looks_like_image_url(link.get('href')):
            link['href'] = local

    if changed and not dry_run:
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(str(soup))
    return changed


def post_title(post_dir, fallback):
    """Best-effort real post title from index.html (the <i><a> header text)."""
    index_path = os.path.join(post_dir, 'index.html')
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'lxml')
    except OSError:
        return fallback
    italic = soup.find('i')
    if italic:
        link = italic.find('a')
        text = (link.get_text() if link else italic.get_text()).strip()
        if text:
            return text
    return fallback


def collect_posts(domain_dir):
    """Group posts in domain_dir into year -> month -> day -> [(dt, title, href)].

    Undated/unparseable folders are grouped under year None.
    """
    groups = {}
    for name in sorted(os.listdir(domain_dir)):
        post_dir = os.path.join(domain_dir, name)
        if not os.path.isdir(post_dir):
            continue
        if not os.path.isfile(os.path.join(post_dir, 'index.html')):
            continue
        m = POST_DIR_RE.match(name)
        if m:
            y, mo, d, hh, mm, ss, slug = m.groups()
            year, month, day = int(y), int(mo), int(d)
            try:
                dt = datetime.datetime(year, month, day, int(hh), int(mm), int(ss))
            except ValueError:
                dt = None
        else:
            year = month = day = None
            slug = name
            dt = None
        title = post_title(post_dir, slug)
        href = name + '/index.html'
        groups.setdefault(year, {}).setdefault(month, {}).setdefault(day, []).append((dt, title, href))
    return groups


# Client-side toggle that reverses post order at every grouping level. Reversing
# an ascending list yields a descending one, so each click just flips the DOM:
# year sections (Undated pinned last), month sections within each year, and the
# <li> posts within each month's <ul>.
SORT_SCRIPT = '''<script>
(function () {
  var btn = document.getElementById('sort-toggle');
  var container = document.getElementById('posts');
  if (!btn || !container) return;
  var newestFirst = false;

  function reverseInto(parent, selector, pinUndated) {
    var items = Array.prototype.filter.call(parent.children, function (el) {
      return el.matches(selector);
    });
    var pinned = pinUndated
      ? items.filter(function (el) { return el.classList.contains('undated'); })
      : [];
    var rest = pinUndated
      ? items.filter(function (el) { return !el.classList.contains('undated'); })
      : items;
    rest.reverse();
    rest.concat(pinned).forEach(function (el) { parent.appendChild(el); });
  }

  btn.addEventListener('click', function (e) {
    e.preventDefault();
    reverseInto(container, 'section.year', true);
    container.querySelectorAll('section.year').forEach(function (year) {
      reverseInto(year, 'section.month', false);
    });
    container.querySelectorAll('section.month > ul').forEach(function (ul) {
      reverseInto(ul, 'li', false);
    });
    newestFirst = !newestFirst;
    btn.textContent = newestFirst
      ? 'Sort: Descending'
      : 'Sort: Ascending';
  });
})();
</script>'''


def render_home(groups):
    """Render the grouped posts as a nested-list home.html.

    Posts render oldest-first by default (years, months and the days within
    each month all ascending, so 1 -> 31); the "Undated" group sorts last. A
    small toggle link reverses the order client-side (see SORT_SCRIPT).
    """
    out = [
        '<!DOCTYPE html>',
        '<html><head><meta charset="UTF-8"><title>Posts</title>',
        '<style>body{font-family:sans-serif;max-width:50em;margin:2em auto;padding:0 1em}'
        'h2{margin:1em 0 .2em}h3{margin:.6em 0 .1em;color:#444}'
        'ul{margin:.2em 0 .6em;list-style:none;padding-left:1em}'
        'li{margin:.15em 0}.day{color:#888;margin-right:.5em}'
        '#sort-toggle{cursor:pointer}</style>',
        '</head><body>',
        '<h1>Posts</h1>',
        '<small><a href="#" id="sort-toggle">Sort: Ascending</a></small>',
        '<div id="posts">',
    ]

    def keyed_asc(d):  # items by key, ascending, with None (undated) last
        return sorted(d.items(),
                      key=lambda kv: (kv[0] is None, kv[0] if kv[0] is not None else 0))

    for year, months in keyed_asc(groups):
        out.append('<section class="year{}">'.format(' undated' if year is None else ''))
        out.append('<h2>{}</h2>'.format('Undated' if year is None else year))
        for month, days in keyed_asc(months):
            out.append('<section class="month">')
            if month is not None:
                month_name = datetime.date(2000, month, 1).strftime('%B')
                out.append('<h3>{}</h3>'.format(month_name))
            out.append('<ul>')
            for day, posts in keyed_asc(days):
                posts_sorted = sorted(
                    posts,
                    key=lambda p: (p[0] is None, p[0] or datetime.datetime.min),
                )
                for dt, title, href in posts_sorted:
                    day_label = '{:02d}'.format(day) if day is not None else ''
                    out.append(
                        '<li><span class="day">{}</span><a href="{}">{}</a></li>'.format(
                            day_label, html.escape(href, quote=True), html.escape(title)
                        )
                    )
            out.append('</ul>')
            out.append('</section>')
        out.append('</section>')

    out.append('</div>')
    out.append(SORT_SCRIPT)
    out.append('</body></html>')
    return '\n'.join(out)


def process_domain(domain_dir, dry_run):
    domain_dir = os.path.abspath(domain_dir)
    print('Domain: ' + domain_dir)
    total_posts = 0
    total_imgs = 0
    for name in sorted(os.listdir(domain_dir)):
        post_dir = os.path.join(domain_dir, name)
        if not os.path.isdir(post_dir):
            continue
        if not os.path.isfile(os.path.join(post_dir, 'index.html')):
            continue
        total_posts += 1
        n = localize_post(post_dir, dry_run)
        total_imgs += n
        if n:
            print('  {}{}: {} image link(s) localized'.format(
                '[dry-run] ' if dry_run else '', name, n))

    groups = collect_posts(domain_dir)
    home_path = os.path.join(domain_dir, 'index.html')
    if not dry_run:
        with open(home_path, 'w', encoding='utf-8') as f:
            f.write(render_home(groups))
    print('  {}{} ({} posts, {} image links localized)'.format(
        '[dry-run] would write ' if dry_run else 'wrote ', home_path,
        total_posts, total_imgs))


def process_new_posts(domain_dir, new_post_dirs, dry_run=False):
    """Localize images for only the given new post dirs, then rebuild the home menu.

    Unlike process_domain, this never rewrites the index.html of posts outside
    new_post_dirs (e.g. posts skipped during download because they already
    existed). The home navigation is still rebuilt from every post in domain_dir
    so the menu stays complete. Called by the downloader's -pp mode.
    """
    domain_dir = os.path.abspath(domain_dir)
    print('Postprocessing new posts in: ' + domain_dir)
    new_dirs = sorted({os.path.abspath(d) for d in new_post_dirs})
    total_imgs = 0
    processed = 0
    for post_dir in new_dirs:
        if not os.path.isfile(os.path.join(post_dir, 'index.html')):
            continue
        processed += 1
        n = localize_post(post_dir, dry_run)
        total_imgs += n
        if n:
            print('  {}{}: {} image link(s) localized'.format(
                '[dry-run] ' if dry_run else '', os.path.basename(post_dir), n))

    groups = collect_posts(domain_dir)
    total_in_menu = sum(
        len(posts)
        for months in groups.values()
        for days in months.values()
        for posts in days.values()
    )
    home_path = os.path.join(domain_dir, 'index.html')
    if not dry_run:
        with open(home_path, 'w', encoding='utf-8') as f:
            f.write(render_home(groups))
    print('  {}{} ({} new posts processed, {} image links localized, {} posts in menu)'.format(
        '[dry-run] would write ' if dry_run else 'wrote ', home_path,
        processed, total_imgs, total_in_menu))


def looks_like_domain_dir(path):
    """A domain folder has >=1 subdir containing index.html."""
    if not os.path.isdir(path):
        return False
    for name in os.listdir(path):
        sub = os.path.join(path, name)
        if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, 'index.html')):
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description='Localize images + build home nav for a downloaded blogspot tree.')
    ap.add_argument('folders', nargs='*', help='Domain folder(s) to process. Default: scan current dir.')
    ap.add_argument('--dry-run', action='store_true', help='Report changes without writing.')
    args = ap.parse_args()

    folders = args.folders
    if not folders:
        folders = [os.path.join('.', n) for n in sorted(os.listdir('.'))
                   if looks_like_domain_dir(os.path.join('.', n))]
        if not folders:
            print('No domain folders found in current directory. Pass folder(s) explicitly.')
            sys.exit(1)

    for folder in folders:
        if not os.path.isdir(folder):
            print('Skipping (not a directory): ' + folder)
            continue
        process_domain(folder, args.dry_run)
    print('Done')


if __name__ == '__main__':
    main()
