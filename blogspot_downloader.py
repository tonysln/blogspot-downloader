import traceback, shutil, resource
import sys, os, re, time, datetime
import readline #https://stackoverflow.com/questions/56274748/how-to-navigate-the-text-cursor-in-pythons-input-prompt-with-arrow-keys
from dateutil import parser as date_parser #need `as` or else conflict name with ArgumentParser
import unicodedata
import feedparser #for rss feed mode
import pdfkit #for pdf #also need `sudo apt install wkhtmltopdf`
from urllib.request import urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse
import urllib.request
import html
from bs4 import BeautifulSoup, SoupStrainer
import argparse
import locale, contextlib
import tempfile

parser = argparse.ArgumentParser(description='Blogspot Downloader')
args = ""

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.84 Safari/537.36'
#prevent image too big and need to scroll
#only epub, don't put it in pdf, it will causes image not appear
img_css_style='<style>img { display: block; padding: 5px; max-height: 100%; max-width: 100%;}</style>' 

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
    value = unicodedata.normalize('NFKD', value)
    value = re.sub('[-/\s]+', ' ', value, re.UNICODE)
    return value

def replacer(s):
    s = s.replace('\\x26', "&")
    s = html.unescape(s)
    for u,v in zip(['’', "“", "”", '—', '–', '…', '®', '&'], ["'", '"', '"', '--', '-', '...', '(R)', '&amp;']):
        s = s.replace(u,v)
    return s

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


def print_rss_err():
    print('\nSeems like no permission to access rss feed, consider use -a OR -1 option to scrape in web mode. Or check your url typo OR network. Tip: you may lucky to find feed url by right-click on the webpage and choose "View Page Source", then search for "rss" keyword\n')

def download(url, h, d_name, ext):
    global download_once
    global init_url_once
    global img_css_style

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
            data = soup.findAll('link', attrs={'type':'application/rss+xml'})
            if not data: #https://github.com/RSS-Bridge/rss-bridge/issues/566 only has atom
                data = soup.findAll('link', attrs={'type':'application/atom+xml'})
            if not data: 
                data = soup.findAll('a', attrs={'href':'/rss/'}) #https://blog.google/products/
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
            print_rss_err()
            
    count = 0
    for tt in t:
        count+=1
        title_raw = ''
        title_is_link = False
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
            for feed_links in tt['links']:
                if feed_links['rel'] == 'alternate':
                    visit_link = feed_links['href']
            title_raw = tt['title'].strip()
            title_pad = title_raw + ' '
            if not tt['title']: #epub got problem copy link from text, so epub always shows link
                tt['title'] = visit_link
                title_is_link = True
            img_css_style = ''

            author = tt.get('author_detail', {}).get('name')
            if not author:
                author = tt.get('site_name', '') #https://blog.google/rss/

            h = '<div><small>' + author + ' ' +  t_date + '<br/><i>' + title_pad + '<a style="text-decoration:none;color:black" href="' + visit_link + '">' + tt['title'] + '</a></i></small><br/><br/></div>' + img_css_style
            #<hr style="border-top: 1px solid #000000; background: transparent;">

            media_content = ''
            try:
                if 'media_content' in tt: #wordpress/blog.google got list of images with link, e.g. darrentcy.wordpress.com
                    for tm in tt['media_content']:
                       #pitfall: python 3 dict no has_key() attr
                        if ('medium' in tm) and (tm['medium'] == 'image') and 'url' in tm:
                            media_content += '<img src="' + tm['url'] + '" >'
                            #media_content += '<img style="display: block; max-height: 100%; max-width: 100%" src="' + tm['url'] + '" >'
                #[UPDATE] shouldn't do like that, since thumbnails of feeds normally duplicated with feed without media_content
                #... which seems act as single thumbnail on webpage scraping metadata usage only.
                #... and seems like https://gigaom.com/feed/ thumbnail is not showing in webpage.
                #elif 'media_thumbnail' in tt: #https://gigaom.com/feed/ only has thumbnail
                #    for tm in tt['media_thumbnail']:
                #        if 'url' in tm:
                #            media_content += '<img src="' + tm['url'] + '" >'
            except Exception as e:
                print(e)
                print('parse media error')

            h = '<head><meta charset="UTF-8"></head><body><div align="center">' + h + tt['summary'].replace('<div class="separator"', '<div class="separator" align="center" ') + media_content + '</div></body>'
            #h = '<head><meta charset="UTF-8"></head><body><div align="center">' + h + tt['summary'].replace('<br /><br /><br />', '<br />') + media_content + '</div></body>'

            title = tt['title']
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

        if title_is_link: #else just leave slash with empty
            title = '/'.join(title.split('/')[-3:])

        fname = os.path.join( d_name, slugify(title) )
        title = replacer(title)
        #fname = os.path.join( d_name, slugify(title.decode('utf-8')))
        fname = os.path.join( d_name, slugify(title))

        fpath = os.path.join( os.getcwd(), fname )
        check_path = os.path.join( fpath + ext )

        if (not download_once) and os.path.exists( check_path ):
            fpath = fpath + '_' + str(int(time.time())) + ext
        else:
            fpath += ext

        print("file path: " + fpath)
        if args.all:
            try:
                pdfkit.from_url(t_url, fpath)
            except IOError as ioe:
                print("pdfkit IOError")
        else:
            try:
                h = replacer(h)
                pdfkit.from_string(h, fpath)
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
    data = soup.findAll('a',attrs={'class':'post-count-link'})
    if not len(data):
        case = 1
        data = soup.findAll('li',attrs={'class':'archivedate'})
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
    if args.url: url = args.url
    else: url = input('URL: ').strip()

    url = process_url(url)
    
    parsed_uri = urlparse(url)
    netloc = '{uri.netloc}/'.format(uri=parsed_uri)
    d_name = slugify(netloc)
    if not args.one and not os.path.isdir(d_name):
        os.makedirs(d_name)

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
