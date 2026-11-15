FROM nginx:alpine

COPY deploy/prototype-preview/nginx.conf /etc/nginx/conf.d/default.conf
COPY design-demos/shenglang-web-prototype/app/dist/ /usr/share/nginx/html/
COPY design-demos/shenglang-web-prototype/provider/ /usr/share/nginx/html/provider/
COPY design-demos/shenglang-web-prototype/watch.js /usr/share/nginx/html/watch.js
COPY deploy/prototype-preview/all-pages.html /usr/share/nginx/html/all-pages.html
