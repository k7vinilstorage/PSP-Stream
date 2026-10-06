/*
 * Idioma dos textos do EBOOT: inglês (padrão) ou português, com lang=pt no
 * server.txt ou no item "Language / Idioma" da tela de configuração. Só
 * ASCII: a fonte do PSP não tem acentos.
 */
#ifndef PSPSTREAM_LANG_H
#define PSPSTREAM_LANG_H

extern int ps_lang_pt; /* 1 = português; definido em config.c */

#define T(en, pt) (ps_lang_pt ? (pt) : (en))

#endif
