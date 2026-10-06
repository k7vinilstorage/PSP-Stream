- **Um PSP por vez.** No Wolf, também um PSPStream por Wolf
  ([Wolf por dentro](Wolf-por-dentro#limites-do-wolf-que-só-com-a-api-não-dá-para-mudar)).
- **Só na rede local.** O stream não tem autenticação nem criptografia, e o
  PSP precisa estar na mesma rede do PC (porta 5123 UDP e TCP). A interface
  web pode ter senha, mas ela vai em HTTP, sem criptografia
  ([Interface web](Interface-web#na-rede-local-com-senha)).
- **Servidor só para Linux** por enquanto. O plano do servidor de Windows
  está em [Servidor para Windows](Servidor-para-Windows).
- **Sem microfone**: o som vai só do PC para o PSP.
- **O som só vai pelo UDP** (o padrão).
- **480x272 fixo para H.264**; resoluções menores (só JPEG) aparecem
  centralizadas, sem ampliação.
- **A captura KMS não mostra o cursor do mouse.**
- **O decoder do PSP segura 2 frames**, então os frames P custam 3 decodes
  (10,6 ms) em vez de 1.
- **O PSP só fala 802.11b** em 2,4 GHz, o que limita a vazão a ~380-460 KB/s
  ([Desempenho](Desempenho)).
