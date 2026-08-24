import traceback, shutil, resource
import sys, os, re, time, datetime
import hashlib
import io
import uuid
import readline #https://stackoverflow.com/questions/56274748/how-to-navigate-the-text-cursor-in-pythons-input-prompt-with-arrow-keys
from dateutil import parser as date_parser #need `as` or else conflict name with ArgumentParser
import unicodedata
import feedparser #for rss feed mode
import pdfkit #for pdf #also need `sudo apt install wkhtmltopdf`
from urllib.request import urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse, unquote
import urllib.request
import html
from bs4 import BeautifulSoup, SoupStrainer
import argparse
import locale, contextlib
import tempfile
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

parser = argparse.ArgumentParser(description='Blogspot Downloader')
args = ""

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.84 Safari/537.36'

temp_dir_ext = ".blogspot-downloader.temp"
sys_tmp_dir = tempfile.gettempdir()

soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))

download_once = False #if want support interactive, then need to changed this logic
init_url_once = False

@contextlib.contextmanager
def setlocale(*args, **kw):
    saved = locale.setlocale(locale.LC_ALL)
    yield locale.setlocale(*args, **kw)
    locale.setlocale(locale.LC_ALL, saved)

def slugify(value):
    value = unicodedata.normalize('NFC', value)
    value = re.sub('[-/\\s]+', ' ', value, flags=re.UNICODE)
    return value.strip()

def replacer(s):
    s = s.replace('\\x26', "&")
    s = html.unescape(s)
    for u,v in zip(['’', "“", "”", '—', '–', '…', '®', '&'], ["'", '"', '"', '--', '-', '...', '(R)', '&amp;']):
        s = s.replace(u,v)
    return s

def looks_like_image_url(u):
    path = urlparse(u).path.lower()
    return path.endswith((
        '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.bmp',
        '.heic', '.heif', '.tif', '.tiff', '.avif',
    )) \
        or ('googleusercontent.com' in u) or ('bp.blogspot.com' in u)

def upgrade_blogspot_image_url(u):
    #blogspot/blogger images embed a size token; rewrite it to the original (s0).
    #the size token may carry one or more letter suffixes, e.g. -c (crop) or -h
    #(an HTML wrapper page, used by old pre-2011 posts). we must strip them all,
    #otherwise /s1600-h/ returns a 388-byte HTML stub instead of the image binary.
    if ('googleusercontent.com' in u) or ('blogspot.com' in u) or ('blogger.com' in u):
        u = re.sub(r'/s\d+(-[a-z]+)*/', '/s0/', u)
        u = re.sub(r'/w\d+-h\d+(-[a-z]+)*/', '/s0/', u)
        u = re.sub(r'=s\d+(-[a-z]+)*(?=$|\?|&)', '=s0', u)
        u = re.sub(r'=w\d+-h\d+(-[a-z]+)*(?=$|\?|&)', '=s0', u)
    return u

def image_output(data, content_type):
    """Return (bytes, extension), converting non-PDF-safe images to JPEG."""
    content_type = content_type.split(';', 1)[0].strip().lower()
    probe = data[:4096].lstrip().lower()
    if data.startswith(b'\xff\xd8\xff'):
        return data, '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return data, '.png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return data, '.gif'
    if content_type == 'image/svg+xml' or b'<svg' in probe:
        return data, '.svg'

    try:
        with Image.open(io.BytesIO(data)) as source:
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            if image.mode in ('RGBA', 'LA') or (
                    image.mode == 'P' and 'transparency' in image.info):
                rgba = image.convert('RGBA')
                background = Image.new('RGB', rgba.size, 'white')
                background.paste(rgba, mask=rgba.getchannel('A'))
                image = background
            else:
                image = image.convert('RGB')
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=90, optimize=True)
            return output.getvalue(), '.jpg'
    except Exception as e:
        print('Failed to decode image (' + repr(e) + ')')
        return None, None


def download_image(url, dest_dir, _depth=0):
    """Download one image and return its ASCII-safe local filename."""
    if url.startswith('//'): #protocol-relative url
        url = 'https:' + url
    try:
        req = urllib.request.Request(url, data=None, headers={'User-Agent': UA})
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            ctype = resp.headers.get('Content-Type', '')
    except Exception as e:
        print('Failed to download image: ' + url + ' (' + repr(e) + ')')
        return
    #some old blogger image links return an HTML wrapper page (e.g. the -h size
    #variant) rather than the image bytes. don't save that as an image; instead
    #recover the real image url embedded in the wrapper and retry once.
    if ctype.lower().startswith('text/html') or data[:512].lstrip().lower().startswith((b'<html', b'<!doctype html')):
        if _depth == 0:
            inner = BeautifulSoup(data, "lxml").find('img')
            inner_src = inner.get('src') if inner else None
            if inner_src:
                inner_src = upgrade_blogspot_image_url(inner_src)
                print('Image url returned an HTML wrapper, retrying embedded image: ' + inner_src)
                return download_image(inner_src, dest_dir, _depth + 1)
        print('Skipping non-image (HTML) response for image: ' + url)
        return None

    data, extension = image_output(data, ctype)
    if data is None:
        print('Skipping unsupported image: ' + url)
        return None
    name = uuid.uuid4().hex + extension
    try:
        with open(os.path.join(dest_dir, name), 'wb') as f:
            f.write(data)
    except OSError as e:
        print('Failed to save image: ' + name + ' (' + repr(e) + ')')
        return None
    return name

def save_post_images(post_html, dest_dir):
    """Download post images, rewrite their local references, and return HTML."""
    soup = BeautifulSoup(post_html, "lxml")
    saved = {}
    for img in soup.find_all('img'):
        src = img.get('src')
        if not src:
            continue
        candidate = src
        link = img.find_parent('a')
        if link and link.get('href') and looks_like_image_url(link.get('href')):
            candidate = link.get('href')
        candidate = upgrade_blogspot_image_url(candidate)
        if candidate not in saved:
            saved[candidate] = download_image(candidate, dest_dir)
        local_name = saved[candidate]
        if not local_name:
            continue
        img['src'] = local_name
        #drop source sizing so the stylesheet alone decides how wide images render
        for attribute in ('srcset', 'data-src', 'data-original', 'data-lazy-src',
                          'width', 'height', 'style'):
            img.attrs.pop(attribute, None)
        picture = img.find_parent('picture')
        if picture:
            #unwrap, never decompose: the parser can nest the img inside <source>
            for source in picture.find_all('source'):
                source.unwrap()
        #clicking a photo opens the local file: repoint an existing image link,
        #or add one, but leave a text link that happens to wrap the image alone
        if link and (looks_like_image_url(link.get('href') or '')
                     or not link.get_text(strip=True)):
            link['href'] = local_name
        elif not link:
            (picture or img).wrap(soup.new_tag('a', href=local_name))
    content = soup.body if soup.body else soup
    return ''.join(str(child) for child in content.contents)


def normalized_title(value):
    return ' '.join(html.unescape(value or '').split()).casefold()


def prepare_summary(summary, title):
    """Remove a leading feed heading when it repeats the post title."""
    soup = BeautifulSoup(summary or '', 'lxml')
    content = soup.body if soup.body else soup
    first_heading = content.find(re.compile(r'^h[1-6]$'))
    if first_heading and normalized_title(first_heading.get_text()) == normalized_title(title):
        first_heading.decompose()
    return ''.join(str(child) for child in content.contents)


def render_post(title, author, post_date, original_url, body_html):
    safe_title = html.escape(title)
    safe_author = html.escape(author or '')
    safe_date = html.escape(post_date or '')
    safe_url = html.escape(original_url, quote=True)
    meta = ' '.join(value for value in (safe_author, safe_date) if value)
    return (
        '<!DOCTYPE html>\n'
        '<html><head><meta charset="UTF-8">'
        '<title>{}</title><link rel="canonical" href="{}">'
        '<link rel="stylesheet" href="../style.css"></head>'
        '<body class="post">'
        '<nav class="backlink"><a href="../index.html">Back</a></nav>'
        '<article><header>'
        '<p class="meta">{}</p><h1><a class="permalink" href="{}">{}</a></h1>'
        '</header><div class="content">{}</div></article></body></html>'
    ).format(safe_title, safe_url, meta, safe_url, safe_title, body_html)


def url_digest(url):
    return hashlib.sha1(url.encode('utf-8')).hexdigest()[:8]


def url_slug(url):
    """ASCII-safe folder name part derived from the post url."""
    segment = unquote(urlparse(url).path.rstrip('/').rsplit('/', 1)[-1])
    segment = os.path.splitext(segment)[0]
    segment = unicodedata.normalize('NFKD', segment).encode('ascii', 'ignore').decode('ascii')
    segment = re.sub(r'[^A-Za-z0-9]+', '-', segment).lower()[:60].strip('-')
    return segment or url_digest(url) #urls whose slug is non-latin or empty


def post_permalink(post_dir):
    """Original url recorded in an already downloaded post, if readable."""
    try:
        with open(os.path.join(post_dir, 'index.html'), 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'lxml')
    except OSError:
        return None
    permalink = soup.find('a', class_='permalink')
    return permalink.get('href') if permalink else None


def post_dir_for(domain_dir, stamp, url):
    """Folder for a post: date + url slug, so the same url always maps here."""
    path = os.path.join(domain_dir, stamp + '_' + url_slug(url))
    if os.path.isdir(path) and post_permalink(path) not in (None, url):
        path += '-' + url_digest(url) #two different posts share date and slug
    return path


def ensure_stylesheet(domain_dir):
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'style.css')
    shutil.copyfile(source, os.path.join(domain_dir, 'style.css'))

def rm_tmp_files():
    for root, dirs, files in os.walk(sys_tmp_dir):
        for fname in files:
            path = os.path.join(root, fname)
            if (len(path) == 14) and path.startswith(os.path.join(sys_tmp_dir, "tmp")): #it may remove wrongly if you have tmpt<SOTDi> file, but it's under module and I don't know how to change its prefix, lolr
                #print('should remove: ' + path)
                try:
                    os.remove(path)
                except OSError as e:
                    print('Failed to remove file')

def parse_locale(s):
    try:
        with setlocale(locale.LC_TIME, args.locale):
            return date_parser.parse(s).strftime('%B %d, %Y, %H:%M %p')
    except locale.Error as e:
        print('\nPlease provide enabled locale alias in your system, e.g. zh_CN.UTF-8. In Linux, you may comment out desired locale in /etc/locale.gen file and then run `sudo locale-gen` to enable it\n')
        sys.exit(-1)

def process_url(url):
    if (url.startswith("'") and url.endswith("'")) or (url.startswith('"') and url.endswith('"')):
        url = url[1:-1]
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

def process_rss_link(url):
    if '?' not in url: url += '?'
    parsed_url = urlparse(url)
    if '{uri.netloc}'.format(uri=parsed_url).endswith('wordpress.com'): #do not blindly mix start-index to paged or else `parsed_url.query.rindex('paged=')` later got exception since its right side is non-int, i.e. int(<page_number>&start-index=) throws error
        if 'paged=' not in url: #wordpress
            url+='&paged=1'
    elif 'start-index=' not in url:
        url+='&start-index=1&max-results=25'
    return url.replace('&alt=rss', '').replace('?alt=rss', '?').replace('?&', '?') #to prevent no next rss page link


def download(url, h, d_name, ext):
    global download_once
    global init_url_once

    #e.g. 'https://diannaoxiaobai.blogspot.com/?action=getTitles&widgetId=BlogArchive1&widgetType=BlogArchive&responseType=js&path=https://diannaoxiaobai.blogspot.com/2018/'
    visit_link = url
    orig_url = url
    if args.all:
        y_url = url + "/?action=getTitles&widgetId=BlogArchive1&widgetType=BlogArchive&responseType=js&path=" + h
        print("Scraping year... " + y_url)
        try:
            r = urlopen(y_url).read()
        except HTTPError as he:
            print('\nNote that -a -s only allow if url has /year/[month] format, pls check your url\n')
            os._exit(1)
        r = r.decode('utf-8')
        t = r.split("'title'")
        t = t[1:]
    else:
        url = process_rss_link(url)
        if not args.log_link_only:
            print("Scraping rss feed... " + url)
        r = feedparser.parse(url) #, request_headers={'User-Agent': UA, 'Referer': url}) #I noticed https://blog.mozilla.org/security/feed/1 (/1 non exist) is working in feedparser, lolr
        #print(r.headers)
        t = r['entries']
        #if (not t) or ("link" not in r['feed'].keys()): #if got entries then whe need retry ? no need check link
        if (not init_url_once) and (not t): #'User does not have permission to read this blog.' of rss feed come here
            init_url_once = True
            #parsed_url = urlparse(url)
            #if not '{uri.netloc}'.format(uri=parsed_url).endswith('wordpress.com'):
            try:
                print("Try to scrape rss feed url automatically ... " + orig_url)
                ##r = urlopen(orig_url).read() #https://medium.com/bugbountywriteup got check UA if urllib2 UA then not authorized
                req = urllib.request.Request(orig_url, data=None, headers={ 'User-Agent': UA })
                r = urllib.request.urlopen(req).read()
            except Exception as e:
                print(e)
                print("Request webpage failed, please check your network OR authorized to access that url.")
                os._exit(1) #don't use sys.exit(-1) if don't want to traceback to main() to print exception
            soup = BeautifulSoup(r, "lxml")
            data = soup.find_all('link', attrs={'type':'application/rss+xml'})
            if not data: #https://github.com/RSS-Bridge/rss-bridge/issues/566 only has atom
                data = soup.find_all('link', attrs={'type':'application/atom+xml'})
            if not data: 
                data = soup.find_all('a', attrs={'href':'/rss/'}) #https://blog.google/products/
            if data:
                url = data[0].get("href")
                url = process_rss_link(url)
                if url.startswith('/'): #http://sectools.org/tag/sploits/ only has href="/feed/"
                    parsed_orig_uri = urlparse(orig_url)
                    url = '{uri.scheme}://{uri.netloc}'.format(uri=parsed_orig_uri) + url
                print("Scraping rss feed one more time ... " + url)
                r = feedparser.parse(url)
                t = r['entries']
                if not t:
                    t = []
            else:
                t = []
        else: #unlike blogspot, wordpress always got t, so need set true here
            init_url_once = True
        parsed_url = urlparse(url)
        is_wordpress = '{uri.netloc}'.format(uri=parsed_url).endswith('wordpress.com')
        if not is_wordpress: #only check next if 1st check is False, or lese 2nd check override 1st result
            try:
                if 'keys' in dir(r):
                    is_wordpress = r.get('feed', {}).get('generator', '').startswith('https://wordpress.org/')
            except Exception as e:
                print('parse generator error', e)
        if is_wordpress and t: #increment paged only if current page got entries, i.e. t
            #parsed_keys = urlparse.parse.parse_qs(parsed_url.query) #my python 2 don't have parse_qs
            if 'paged=' in parsed_url.query:
                wp_paged_v = int(parsed_url.query[parsed_url.query.rindex('paged=') + len('paged='):])
                #uri.path default prefix with '/' if not empty, so don't set '/' after netloc or else keep increase '////...' in each page
                url = '{uri.scheme}://{uri.netloc}{uri.path}?'.format(uri=parsed_url) + parsed_url.query.replace('paged=' + str(wp_paged_v), 'paged=' + str(wp_paged_v+1))
            else:
                url = ''
                print('no next') 
        elif ("keys" in dir(r)) and ('links' in r['feed'].keys()):
            l = r['feed']['links']
            #print(r['feed'])
            if l:
                got_next = False
                for ll in l:
                    #print('hola' + repr(ll))
                    if ll['rel'] == 'next':
                        #if ll['href'] != url: #don't have next link is same case to test
                        url = ll['href']
                        got_next = True
                        break;
                if not got_next:
                    url = ''
            else:
                url = ''
        elif not t: #no need care if next page rss index suddenly change and no content case
            url = ''
            print('\nSeems like no permission to access rss feed, consider use -a OR -1 option to scrape in web mode. Or check your url typo OR network. Tip: you may lucky to find feed url by right-click on the webpage and choose "View Page Source", then search for "rss" keyword\n')
            
    count = 0
    for tt in t:
        count+=1
        title_raw = ''
        if not args.all:
            #e.g. parser.parse('2012-12-22T08:36:46.043-08:00').strftime('%B %d, %Y, %H:%M %p')
            h = ''
            #https://github.com/RSS-Bridge/rss-bridge/commits/master.atom only has 'updated'
            post_date = tt.get('published', tt.get('updated', ''))
            t_date = ''
            try:
                if args.locale:
                    t_date = parse_locale(post_date)
                else:
                    t_date = date_parser.parse(post_date).strftime('%B %d, %Y, %H:%M %p')
            except ValueError: #Unknown string format, e.g. https://www.xul.fr/en-xml-rss.html got random date format such as 'Wed, 29 Jul 09 15:56:54  0200'
                t_date = post_date
            try: #sortable, filesystem-safe stamp for the per-post subfolder name
                post_dt_str = date_parser.parse(post_date).strftime('%Y-%m-%d_%H%M%S')
            except (ValueError, TypeError):
                post_dt_str = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
            for feed_links in tt['links']:
                if feed_links['rel'] == 'alternate':
                    visit_link = feed_links['href']
            title = html.unescape(tt.get('title', '')).strip() or visit_link
            title_raw = title
            domain_dir = os.path.join(os.getcwd(), d_name)
            post_dir = post_dir_for(domain_dir, post_dt_str, visit_link)
            print(post_dir)
            if os.path.isdir(post_dir):
                print('Post already exists, skipping: ' + visit_link)
                continue

            author = tt.get('author_detail', {}).get('name')
            if not author:
                author = tt.get('site_name', '') #https://blog.google/rss/

            media_content = ''
            try:
                if 'media_content' in tt: #wordpress/blog.google got list of images with link, e.g. darrentcy.wordpress.com
                    for tm in tt['media_content']:
                       #pitfall: python 3 dict no has_key() attr
                        if ('medium' in tm) and (tm['medium'] == 'image') and 'url' in tm:
                            media_content += '<img src="{}">'.format(
                                html.escape(tm['url'], quote=True))
            except Exception as e:
                print(e)
                print('parse media error')

            body_html = prepare_summary(tt.get('summary', ''), title) + media_content
            t_url = visit_link
        else:
            field = tt.split("'")
            title = field[1]
            title_raw = title.strip()
            t_url = field[5]

        if not args.log_link_only:
            print('\ntitle: ' + title_raw)
            print('link: ' + t_url)
        else:
            print(t_url)

        print('Download html as PDF, please be patient...' + str(count) + '/' + str(len(t)))

        if args.all:
            title = replacer(title)
            slug = slugify(title).strip()[:120] #cap to stay within filename limits
            fname = os.path.join( d_name, slug )
            fpath = os.path.join( os.getcwd(), fname )
            check_path = os.path.join( fpath + ext )

            if (not download_once) and os.path.exists( check_path ):
                fpath = fpath + '_' + str(int(time.time())) + ext
            else:
                fpath += ext

            print("file path: " + fpath)
            try:
                pdfkit.from_url(t_url, fpath)
            except IOError as ioe:
                print("pdfkit IOError")
        else:
            os.makedirs( post_dir, exist_ok=True )

            if args.save_images: #opt-in via -i, off by default
                body_html = save_post_images(body_html, post_dir)

            post_html = render_post(title, author, t_date, visit_link, body_html)
            index_path = os.path.join(post_dir, 'index.html')
            with open(index_path, 'w', encoding='utf-8') as f:
                f.write(post_html)

            if args.no_pdf:
                continue
            fpath = os.path.join(post_dir, 'post' + ext)
            print("file path: " + fpath)
            try:
                pdfkit.from_file(
                    index_path, fpath,
                    options={'enable-local-file-access': None})
            except IOError as ioe:
                print('Exception IOError: ' + repr(ioe))
    return url #return value used for rss feed mode only

def scrape(url, d_name, ext):
    try:
        req = urllib.request.Request(url, data=None, headers={ 'User-Agent': UA })
        r = urllib.request.urlopen(req).read()
    except Exception as e:
        print(e)
        print("Please check your network OR url.")
        os._exit(1)
    soup = BeautifulSoup(r, "lxml")
    case = 0
    data = soup.find_all('a',attrs={'class':'post-count-link'})
    if not len(data):
        case = 1
        data = soup.find_all('li',attrs={'class':'archivedate'})
    year_l = []
    if len(data) == 0:
        print('\nNo data found. You may check your url OR try -f <rss feed url> OR remove -a instead. Also do not use -a if -f added.\n')
        os._exit(1)
    for div in data:
        if case == 0:
            h = div['href']
        else:
            h = div.a.get('href')
        dup = False
        for y in year_l:
            if h.startswith(y):
                dup = True
                break
        if not dup:
            year_l.append(h)
            if args.print_date:
                print(h)
            else:
                download(url, h, d_name, ext)

def process_url(url):
    if (url.startswith("'") and url.endswith("'")) or (url.startswith('"') and url.endswith('"')):
        url = url[1:-1]
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

def main():
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        os.chdir(args.output_dir) #all folder checks/downloads use cwd + relative paths, so this redirects everything

    if args.url: url = args.url
    else: url = input('URL: ').strip()

    url = process_url(url)
    
    parsed_uri = urlparse(url)
    netloc = '{uri.netloc}/'.format(uri=parsed_uri)
    d_name = args.folder_name if args.folder_name else slugify(netloc) #override e.g. if the blog folder was renamed off the netloc
    if not args.one and not os.path.isdir(d_name):
        os.makedirs(d_name)
    if not args.one and not args.all:
        ensure_stylesheet(os.path.join(os.getcwd(), d_name))

    ext = '.pdf'
    
    if args.print_date:
        print('Debugging\n')
        scrape(url, d_name, ext)
    elif args.one:
        d_name = d_name.strip()
        fname = d_name + ext
        fpath = os.path.join(os.getcwd(), fname)
        while os.path.exists(fpath):
            fname = d_name + '_' + str(int(time.time())) + ext
            fpath = os.path.join(os.getcwd(), fname )
        try:
            # [further:0] 'https://thehackernews.com/2019/09/phpmyadmin-csrf-exploit.html' 
            # ... nid -1 -p, can't simply -1
            print('Create single pdf: ' + fpath)
            # test case(need default 3 seconds): https://www.quora.com/Why-does-the-loopback-interface-on-my-computer-has-65536-as-the-MTU-while-other-interfaces-has-1500-as-the-MTU
            pdfkit.from_url(url, fpath, options={'--javascript-delay': args.js_delay*1000})
        except IOError as ioe:
            print("IOError --one: ", ioe)

    elif not args.all:
        print('Download in rss feed mode')
        if args.feed: url = args.feed
        while url:
            url = download(url, url, d_name, ext)
        if args.postprocess:
            import postprocess
            domain_dir = os.path.join(os.getcwd(), d_name)
            postprocess.process_domain(domain_dir, False)
    elif args.single:
        print('Download single year/month in website mode')
        download(url, url, d_name, ext)
    else:
        print('Download all in website mode')
        scrape(url, d_name, ext)
    print("\nDone")


if __name__ == "__main__":
    parser.add_argument('-a', '--all', action='store_true', help='Display website mode instead of rss feed mode. Only support blogspot website but you can try your luck in other site too')
    parser.add_argument('-s', '--single', action='store_true', help='Download based on provided url year/month instead of entire blog, will ignored in rss feed mode and --print_date')
    parser.add_argument('-d', '--print_date', action='store_true', help='Print main date info without execute anything')
    parser.add_argument('--js-delay', dest='js_delay', type=int, default=3, help='Specify delay seconds for -1 -p to have enough time for Javascript to load. Default is 3 seconds.')
    parser.add_argument('-l', '--locale', help='Date translate to desired locale, e.g. -l zh_CN.UTF-8 will shows date in chinese')
    parser.add_argument('-f', '--feed', help='Direct pass full rss feed url. e.g. python blogspot_downloader.py http://www.ulduzsoft.com/feed/ -f http://www.ulduzsoft.com/feed/. Note that it may not able to get previous rss page in non-blogspot site.') #got case not return code, e.g. http://zoczus.blogspot.com/2015/04/plupload-same-origin-method-execution.html , use -a in this case
    parser.add_argument('-1', '--one', action='store_true', help='Scrape url of ANY webpage as single pdf(-p) or epub')
    parser.add_argument('-lo', '--log-link-only', dest='log_link_only', action='store_true', help='print link only log for -f feed, temporary workaround to copy into -1, in case -f feed only retrieve summary.')
    parser.add_argument('-i', '--save-images', dest='save_images', action='store_true', help='In rss feed mode, download post images with UUID names, convert unsupported formats to JPEG, and use local links. Off by default.')
    parser.add_argument('--no-pdf', dest='no_pdf', action='store_true', help='In rss feed mode, skip writing post.pdf. HTML, images, and the home menu are still created.')
    parser.add_argument('-pp', '--postprocess', dest='postprocess', action='store_true', help='In rss feed mode, rebuild the home menu after downloading.')
    parser.add_argument('-o', '--output-dir', dest='output_dir', help='Base folder to download into / check for existing posts. Created if missing. Default: current directory.')
    parser.add_argument('-n', '--folder-name', dest='folder_name', help='Override the blog folder name (default: derived from the url, e.g. testblog.com). Use this if the folder was renamed, e.g. -n testblog, so existing posts are still recognized/skipped.')
    parser.add_argument('url', nargs='?', help='Blogspot url') #must add nargs='?' or else always need url but -f shouldn't need
    args, remaining  = parser.parse_known_args() #don't use normal parse_args() which can't ignore above url
    
    if args.feed: args.url = args.feed
    
    try:
        main()
    except Exception as e:
        if traceback.format_exc() != 'None\n':
            print(traceback.format_exc())
            print("Exception -2") #this one might not called if ctrl+c inside pypub.create_chapter_from_url's urllib3, so we need another finally to do clean_up 
    finally: #https://stackoverflow.com/questions/4606942/why-cant-i-handle-a-keyboardinterrupt-in-python
        #traceback.print_exc() #finally doesn't always means exception, it will run even in normal flow, so no need clean_up in other place
        #temp workaround to suppress none
        f = open(os.devnull, 'w') #don't print anthing for traceback.print_exc
        sys.stdout = f
        if traceback.format_exc() != 'None\n':
            print(traceback.format_exc())
            print("Exception -1")
