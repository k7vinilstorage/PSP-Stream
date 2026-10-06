Os botões do PSP chegam ao PC como um controle de Xbox ou como teclado e
mouse. O perfil se escolhe com `--profile` ou na [Interface web](Interface-web).
Para isso, o servidor precisa do `/dev/uinput`
([Instalação](Instalação#controles-uinput)). No Wolf, os perfis de Xbox
valem dentro do jogo ([Wolf](Wolf#os-controles-no-jogo)).

## Controle de Xbox (`--profile xbox`)

Como no Sunshine, o PC ganha um **controle de Xbox 360 virtual**, com o
mesmo fabricante, modelo, botões e eixos do driver `xpad`. Jogos nativos e
do Proton (SDL), o Steam e o navegador o reconhecem sem configuração. Não
tem vibração: o PSP não tem motor.

O PSP tem menos controles que um Xbox. O resto vem de uma camada:
**segurando SELECT**, os outros botões mudam de função, e **um toque rápido
no SELECT sozinho** vale BACK (View).

| PSP | `xbox` | segurando SELECT |
|---|---|---|
| X / círculo / quadrado / triângulo | A / B / X / Y | L3 / R3 / BACK / Guide |
| direcional | direcional | analógico direito |
| L / R | LT / RT (gatilho inteiro) | LB / RB |
| START | Start | (SELECT + START é o menu do PSP) |
| analógico | analógico esquerdo | analógico esquerdo |

- `xbox-camera`, para jogos 3D: X/círculo/quadrado/triângulo viram o
  **analógico direito** (câmera) e o direcional vira A/B/X/Y (baixo = A,
  direita = B, esquerda = X, cima = Y). Segurando SELECT, o direcional volta
  a ser direcional.
- `xbox-ombros`: L/R = LB/RB e SELECT + L/R = LT/RT.

## Teclado e mouse

Perfil `jogo` (padrão):

| PSP | PC |
|---|---|
| direcional | W A S D |
| X / círculo / quadrado / triângulo | espaço / Ctrl / R / E |
| R / L | clique esquerdo / direito |
| START / SELECT | Esc / Tab |
| analógico | mouse |

Perfil `desktop`: direcional = setas, X/círculo = cliques, SELECT = Alt+Tab.
Perfil `setas`: para emuladores e jogos antigos.

## Perfis próprios

Os perfis ficam em
[`server/keymap.json`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/server/keymap.json)
(as chaves `_ajuda` explicam o formato), com zona morta, curva e velocidade
ajustáveis. Se o PSP sumir com algo apertado, tudo é solto em 0,5 s
(`--input-timeout`). `--input-dry-run` só mostra no log o que seria
injetado.
