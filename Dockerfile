FROM node:lts

WORKDIR /app

RUN git clone --depth=1 https://github.com/zotero/translation-server.git . \
    && git clone --depth=1 https://github.com/zotero/translators.git modules/translators/

COPY translation-server.patch /tmp/translation-server.patch
RUN git apply /tmp/translation-server.patch

RUN npm install

EXPOSE 1969
ENTRYPOINT ["npm", "start"]
