#!/bin/sh
# Gera assets/testcard.jpg (480x272, baseline 4:2:0) com ffmpeg.
#  - barras R G B C M Y: canais trocados (RGBA x BGRA) aparecem na hora
#  - grade de 40 px + diagonais: erro de stride entorta as linhas
#  - borda branca de 2 px: mostra se a imagem foi cortada
set -e
out="${1:-$(dirname "$0")/../assets/testcard.jpg}"
font="${FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf}"
w=480 h=272 bw=80
ffmpeg -y -hide_banner -loglevel error -f lavfi -i "color=c=0x202020:s=${w}x${h}" -frames:v 1 -vf "\
drawgrid=w=40:h=40:t=1:c=0x606060,\
drawbox=x=0:y=0:w=$bw:h=96:c=red:t=fill,\
drawbox=x=$bw:y=0:w=$bw:h=96:c=0x00ff00:t=fill,\
drawbox=x=$((bw*2)):y=0:w=$bw:h=96:c=blue:t=fill,\
drawbox=x=$((bw*3)):y=0:w=$bw:h=96:c=cyan:t=fill,\
drawbox=x=$((bw*4)):y=0:w=$bw:h=96:c=magenta:t=fill,\
drawbox=x=$((bw*5)):y=0:w=$bw:h=96:c=yellow:t=fill,\
drawtext=fontfile=$font:text='R':x=34:y=36:fontsize=28:fontcolor=white,\
drawtext=fontfile=$font:text='G':x=114:y=36:fontsize=28:fontcolor=black,\
drawtext=fontfile=$font:text='B':x=194:y=36:fontsize=28:fontcolor=white,\
drawtext=fontfile=$font:text='C':x=274:y=36:fontsize=28:fontcolor=black,\
drawtext=fontfile=$font:text='M':x=354:y=36:fontsize=28:fontcolor=white,\
drawtext=fontfile=$font:text='Y':x=434:y=36:fontsize=28:fontcolor=black,\
geq=lum='if(lt(abs(X*$h-Y*$w),$w)+lt(abs((${w}-1-X)*$h-Y*$w),$w),235,lum(X,Y))':cb='cb(X,Y)':cr='cr(X,Y)',\
drawtext=fontfile=$font:text='PSPStream':x=(w-tw)/2:y=150:fontsize=40:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=6,\
drawtext=fontfile=$font:text='480x272  4\\:2\\:0':x=(w-tw)/2:y=206:fontsize=18:fontcolor=white,\
drawbox=x=0:y=0:w=$w:h=$h:c=white:t=2,\
format=yuvj420p" -c:v mjpeg -q:v 3 -f image2 "$out"
echo "gerado: $out ($(wc -c < "$out") bytes)"
