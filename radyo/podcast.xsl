<?xml version="1.0" encoding="UTF-8"?>
<!-- BIST Radyo podcast.xml tarayicida acildiginda ham XML agaci yerine bu
     stille bicimlendirilir. Podcast uygulamalari xml-stylesheet PI'sini yok
     sayar; RSS isleyisi degismez. -->
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  exclude-result-prefixes="itunes">
<xsl:output method="html" doctype-system="about:legacy-compat" indent="yes"/>
<xsl:template match="/rss/channel">
<html lang="tr">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title><xsl:value-of select="title"/></title>
<style>
body { margin:0; font-family:"Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; background:#f1f5f9; color:#0f172a; line-height:1.65; }
.wrap { max-width:720px; margin:0 auto; padding:32px 20px 48px; }
.card { background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:20px 24px; margin-bottom:14px; }
h1 { font-size:24px; margin:0 0 6px; }
.aciklama { color:#64748b; margin:0; font-size:14.5px; }
.item { border-top:1px solid #e2e8f0; padding:12px 0; }
.item:first-of-type { border-top:none; }
.baslik { font-weight:700; }
.tarih { color:#64748b; font-size:13px; margin:2px 0 4px; }
audio { width:100%; }
a { color:#0f766e; }
.kucuk { color:#94a3b8; font-size:12px; margin:0; }
.badge { display:inline-block; background:#f0fdfa; color:#0f766e; border:1px solid #99f6e4; border-radius:999px; padding:3px 12px; font-size:12.5px; font-weight:600; margin-left:8px; }
</style>
</head>
<body>
<div class="wrap">
<div class="card">
<h1>📻 <xsl:value-of select="title"/><span class="badge">AI sunucular</span></h1>
<p class="aciklama"><xsl:value-of select="description"/></p>
<p style="margin:12px 0 0; font-size:14px">
<a><xsl:attribute name="href"><xsl:value-of select="link"/></xsl:attribute>&#8592; Siteye dön</a>
&#160;&#160;·&#160;&#160;
<a href="index.html">Radyo sayfası &amp; tüm yayınlar &#8594;</a>
</p>
</div>
<div class="card">
<p style="margin:0 0 4px; font-weight:700">Bölümler</p>
<xsl:for-each select="item">
<div class="item">
<div class="baslik"><xsl:value-of select="title"/></div>
<div class="tarih"><xsl:value-of select="pubDate"/> · <xsl:value-of select="itunes:duration"/> dk</div>
<audio controls="controls" preload="none">
<xsl:attribute name="src"><xsl:value-of select="enclosure/@url"/></xsl:attribute>
</audio>
</div>
</xsl:for-each>
</div>
<div class="card">
<p class="kucuk">Bu adres bir podcast RSS akışıdır: Apple Podcasts, Overcast, AntennaPod gibi uygulamalara ekleyerek abone olabilirsiniz. Kurgusal yapay zeka sunucular • Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.</p>
</div>
</div>
</body>
</html>
</xsl:template>
</xsl:stylesheet>
