#!/usr/bin/env python3

import argparse
import datetime
import html
import os
import re
import shutil
import sys

from bs4 import BeautifulSoup

# post subfolder name: YYYY-MM-DD_HHMMSS_<slug from the original post url>
POST_DIR_RE = re.compile(
    r'^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})_([a-z0-9][a-z0-9-]*)$')


def post_title(post_dir, fallback):
    """Return the visible post title from index.html."""
    index_path = os.path.join(post_dir, 'index.html')
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'lxml')
    except OSError:
        return fallback
    heading = soup.find('h1')
    if heading:
        text = heading.get_text().strip()
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
        '<html><head><meta charset="UTF-8"><title>Posts</title>'
        '<link rel="stylesheet" href="style.css"></head>',
        '<body class="home"><main>',
        '<header><h1>Posts</h1></header>',
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
    out.append('</main></body></html>')
    return '\n'.join(out)


def write_home(domain_dir, dry_run):
    domain_dir = os.path.abspath(domain_dir)
    groups = collect_posts(domain_dir)
    total_posts = sum(
        len(posts)
        for months in groups.values()
        for days in months.values()
        for posts in days.values()
    )
    home_path = os.path.join(domain_dir, 'index.html')
    if not dry_run:
        with open(home_path, 'w', encoding='utf-8') as f:
            f.write(render_home(groups))
        css_source = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'style.css')
        shutil.copyfile(css_source, os.path.join(domain_dir, 'style.css'))
    print('  {}{} ({} posts)'.format(
        '[dry-run] would write ' if dry_run else 'wrote ', home_path,
        total_posts))


def process_domain(domain_dir, dry_run):
    print('Domain: ' + os.path.abspath(domain_dir))
    write_home(domain_dir, dry_run)


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
    ap = argparse.ArgumentParser(description='Build home navigation for a downloaded blogspot tree.')
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
