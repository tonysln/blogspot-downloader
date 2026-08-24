# Blogspot Downloader

> [!NOTE]
> Forked from [blogspot-downloader](https://github.com/limkokhole/blogspot-downloader) by developer **limkokhole**.

This python script download all posts from blogspot and convert into epub or pdf, either in web page looks or rss feed looks.

Not only blogspot, if any webpage contains rss feed, especially for wordpress, then it able to download in rss mode.



## Setup

Ensure you have `wkhtmltopdf` available on your system.

Clone project and install python dependencies listed in `requirements.txt`.


## Run

    python blogspot_downloader.py [url]

## Usage

Download an entire Blogspot blog. Each post is saved as `index.html` and
`post.pdf` in a folder named from the post date and an ASCII slug taken from the
original post url, e.g. `2018-03-04_091500_my-post-title`. The name is
deterministic, so re-running a download maps a post to the same folder and skips
it. The original title remains visible in the HTML and home menu but is not used
in filesystem names.

    python blogspot_downloader.py [blogspot url] 

RSS feed mode: save images (`-i`), rebuild the home menu (`-pp`), and skip PDFs (`--no-pdf`).

    python blogspot_downloader.py -i -pp --no-pdf [blogspot url]
    
Saved images use ASCII UUID filenames and page links are rewritten to those
local files. JPEG, PNG, GIF, and SVG are kept; HEIC and other unsupported
formats are converted to JPEG. Pages share the domain folder's `style.css`.

The `-pp` flag runs `postprocess.py` after an RSS download to rebuild the home
menu. Re-running a download skips any post whose folder already exists.

### Postprocess

Use this tool to regenerate the blog contents page with links sorted by date.
