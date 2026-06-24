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

Download entire blogspot blog. Each post will be saved into a PDF and HTML, organized by subfolders.

    python blogspot_downloader.py [blogspot url] 

RSS feed mode: save images (-i) then auto-run postprocessing (-pp) on the freshly downloaded posts only.

    python blogspot_downloader.py -i -pp [blogspot url]
    
The -pp flag runs postprocess.py automatically after an RSS feed download. It localizes images and rebuilds the home menu, but only touches posts created in that run; posts skipped because their folder already existed are left as-is. Pair it with -i so there are local images to localize. 

### Postprocess

Use this tool to generate a blog contents/menu HTML file with links to all posts, sorted by date.
