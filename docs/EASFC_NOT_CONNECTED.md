# "EAS FC non connecté"

What the header banner means, and why it is a different problem from FUT.

## It is a second Blaze connection, not an HTTP one

`powdllzf.xex.dll` is mapped at `0x89700000` once the title is up, and its
strings name the whole subsystem:

```text
0x89706B08  POWService::PowBlazeDisconnected
0x8970E3A0  connectedToPOW
0x8970E38C  reconnectingToPOW
0x8970D128  connectionState
0x89706250  pal.gt.easfc.ea.com:8094          the session endpoint
0x897061B0  content.lt.easfc.ea.com:8080      the catalogue endpoint
```

`PowBlazeDisconnected` is the important one: the EAS FC session is a **Blaze**
connection to its own server, separate from the one FUT uses. Neither of those
hostnames is among the four the launch patch rewrites -- those are only
`gosredirector.ea.com` and its three siblings -- so the client resolves them for
real, reaches nothing, and reports the banner. There is no error in our journal
because the traffic never comes near us.

## What was changed

The module reads its endpoints from configuration in preference to those
compiled defaults:

```text
0x897085C4  ONLINE/POW_CUSTOMCONTENTURL
0x897085EC  ONLINE/POW_CUSTOMURL
0x897085B4  FIFA_POW_URL
0x89708598  FIFA_POW_CONTENT_SERVER_URL
0x8970857C  FIFA_POW_NUCLEUS_PROXY_URL
```

`OSDK_CORE` now serves all five, pointing the session at the Blaze core port
and the catalogue at the identity server. The retail values give the format:
`host:port` for the session, a URL for the content.

## Unverified

This is a configuration change made from static strings; it has not been seen
to work. If the banner still reads disconnected on the next launch, the next
step is to watch for a connection attempt on the Blaze port from the POW module
rather than assume the key was read at all -- and, failing that, to patch the
hostname in `powdll`'s image the way the launch patch already rewrites the four
in `default.xex`.

It is worth remembering that this is cosmetic for playing: FUT logs in, the club
loads, the market works and packs open with the banner reading disconnected
throughout.

## Measured, 20 August 2026: the module never connects at all

Three things were true at once, and that is what makes this decisive.

**The endpoint strings were rewritten, and stayed rewritten.** Read out of a
*running* title, not out of a patch script's own claim:

    0x89706250: b'192.168.1.40:10041\x00...'
    0x897061B0: b'http://192.168.1.40:18080\x00...'

**The retail ports were redirected too.** `pal.gt.easfc.ea.com:8094` and
`content.lt.easfc.ea.com:8080` were outside the connect hook's port filter all
along, so whichever endpoint the module had kept, its connects had been walking
past the hook and out to the internet. Both ports joined the filter, and the
server was listening on both -- Blaze on 8094, a second HTTP listener on 8080.

**And nothing arrived.** The whole session, from boot to the main menu:

    connexion 1  42124  17:58:34            the redirector
    connexion 2  10041  17:58:34 -> 18:06:37  the title
    connexion 3  10041  18:07:37 -> 18:12:38  the same title, reconnecting

Connections 2 and 3 are sequential, not concurrent: one client that dropped and
came back a minute later, not two. Zero frames on 8094. Zero on 8080. Banner
still reads "EAS FC non connecté".

So the module does not reach the hooked `connect` by either route. Two things
that can be, and they are different problems:

* it never attempts the session, because something upstream of it fails first
  and nothing downstream ever runs;
* or it connects through a path this hook does not cover. The hook is **one
  callsite in `default.xex`**, and `powdllzf` is a separate module with its own
  linked code -- its own copy of DirtySock would have its own callsite, at
  `0x89700000+`, which nothing here has ever looked at.

The second is the cheaper one to test and has never been tried. Everything
about this problem so far has assumed the module borrows the title's networking.

### What this closes

Rewriting the endpoints is finished as a line of work. It was tried as
configuration (`ONLINE/POW_CUSTOMURL` and its four siblings, served through
`OSDK_CORE`), then as an image rewrite, and the image rewrite demonstrably
lands and demonstrably changes nothing. Neither was ever the mechanism, and no
amount of making the write land earlier or more reliably will change that --
which is worth writing down, because "the patcher lost a race" was the standing
explanation for two weeks and it was wrong.

It stays cosmetic for playing: FUT logs in, the club loads, the market works,
packs open, consumables render, with the banner reading disconnected throughout.

## FUT needs an Xbox Live profile, and says so badly

Measured 2026-08-10 by switching profiles on the same console, same launch
patches, same server:

```text
louaY           local profile, never on Xbox Live
                -> "Vous devez etre connecte a Xbox Live et aux serveurs EA"
                -> ZERO requests reach the server

Imskobogota6z   Xbox Live profile
                -> authentication2_login, POST /authentication360,
                   futBoot.xml, user/accountinfo
```

The launch patches read back `PATCHE` in both cases, so this is not them. The
title checks the profile type locally and refuses before opening a socket,
which is why the journal is empty rather than showing a failed attempt.

Worth knowing because the message names Xbox Live *and* the EA servers, and the
project has spent time treating that wording as a server-side problem. With a
local profile it is neither: nothing was ever asked of any server.

The profile selector marks the difference -- `Imskobogota6z` carries an
XBOX LIVE badge, `louaY` and `Player1` do not.


## Mesuré, 24 août 2026 : le module n'appelle jamais `connect`, et le bouton non plus

Le document ci-dessus laissait deux explications et en désignait une comme la
moins chère et jamais essayée : `powdllzf` connecterait par un chemin que le
crochet ne couvre pas, puisque celui-ci est **un site d'appel dans
`default.xex`** et que le module est séparé, avec son propre code lié.

Cette hypothèse est morte. `tools/xam_connect_log_hook.py` se pose sur
`NetDll_connect` dans `xam.xex` -- le goulot que personne ne contourne, quelle
que soit la copie de DirtySock -- et enregistre le LR de l'appelant.

**Ce que la chaîne d'appel apprend au passage.** Le titre ne saute pas
directement sur l'export. Il passe par son shim d'import `0x824CA450`, qui
décale les arguments d'un rang et met `r3 = 1`, puis par la table de thunks en
`0x83C82A64`, qui branche sur `0x81741BF8` **sans lien**. C'est ce `b` sans lien
qui rend la mesure possible : LR porte encore l'appelant d'origine et non un
maillon intermédiaire. Corollaire pratique : à l'entrée de l'export, `r4` est le
socket, `r5` le sockaddr, `r6` la longueur -- et non `r3`/`r4`/`r5`.

**Le témoin, d'abord.** Un redémarrage du serveur Blaze force le titre à
rouvrir ses connexions, et la sonde les voit toutes :

```text
appelant 0x83C8E6D0 (default.xex)  87.106.7.87:42124   le redirecteur
appelant 0x83C8E6D0 (default.xex)  87.106.7.87:10041   Blaze
appelant 0x83C8E6D0 (default.xex)  87.106.7.87:18080   l'identité
appelant 0x81773434 (xam.xex)      ...                 interne à XAM
```

**L'appui, ensuite.** Anneau vidé, crochet vérifié en place et stub relu octet
par octet, écran sur FOOTBALL CLUB, bouton A sur « CONNEXION AUX SERVEURS EAS
FC » :

```text
0 appels à NetDll_connect
```

Écran inchangé -- pas de sablier, pas de dialogue d'erreur, toujours
« Déconnecté ». Puis le même témoin rejoué immédiatement après : douze appels
en quelques secondes. La sonde était donc vivante pendant l'appui.

### Ce que ça ferme, et où ça déplace le problème

Le module ne compose pas. Il ne compose pas mal, il ne compose pas ailleurs :
il ne compose pas. Toute la famille d'explications réseau est close --
endpoints, ports, filtres, copie de DirtySock, chemin non couvert. Aucune
n'était le mécanisme, et aucune ne peut l'être.

Le problème est **en amont de la couche réseau** : quelque chose qui devrait
déclencher la session EAS FC ne s'exécute jamais. Le bouton lui-même n'est pas
une preuve d'intention -- rien ne dit qu'il atteint le module ; il se peut
qu'il soit désarmé bien avant, par le même état qui grise les quatre tuiles.

Les deux pistes qui restent, et elles ne sont plus dans le réseau :

* **Suivre le bouton.** `easfcFlow` et `powCatalogue` sont dans
  `docs/navgraphs/mainfeflow.json` ; l'action de la tuile CATALOGUE est
  `gotoCatalogue`. Savoir si l'appui atteint seulement `powdllzf` se tranche
  avec un crochet sur le point d'entrée de connexion du module, pas sur son
  socket.
* **Chercher la garde.** Le module a des chaînes d'état -- `connectedToPOW`,
  `reconnectingToPOW`, `connectionState` à `0x8970D128`. Une condition lue là
  et jamais satisfaite expliquerait à la fois le bandeau, les tuiles grisées et
  ce silence complet.

Ce qui reste vrai depuis le début : c'est cosmétique pour jouer. FUT se
connecte, le club charge, le marché fonctionne, les pochettes s'ouvrent.


## La garde, trouvée : 24 août 2026

Le module ne compose pas parce qu'une garde à trois termes n'est jamais
satisfaite, et il n'en manque qu'un.

`tools/xbox360_xbdm_dump.py --module powdllzf` sort `.rdata`, `.text` et
`.data` -- 670 Ko, séquentiels, lecture seule ; ce n'est pas le balayage de tas
qui fait tomber la console, et elle n'a pas bronché. Tout ce qui suit est lu
hors ligne.

### Le prédicat

Une fonction en `0x89758D08`, répliquée en ligne à quatre autres endroits
(`0x897334C4`, `0x8973356C`, `0x897335BC`, `0x89733620`) :

```text
0x89758D08  lwz    r11, 0x80(r3)      l'état de connexion
0x89758D0C  cmpwi  r11, 1
0x89758D10  bne    -> retourne 0
0x89758D14  lbz    r11, 0x48c(r3)     POW_IS_ON
0x89758D18  cmplwi r11, 0
0x89758D1C  beq    -> retourne 0
0x89758D20  lbz    r11, 0x84(r3)      un troisième drapeau
0x89758D24  cmplwi r11, 0
```

`POW_IS_ON` est un booléen de configuration, lu une seule fois en `0x89748ED8`
avec **défaut à 1**, et le constructeur l'écrit à 1 en `0x89774040`. Il n'est
donc pas le blocage, et notre serveur n'a pas à le servir.

### L'objet, lu sur la console

Le singleton s'obtient par l'accesseur `0x897745C0`, qui lit le pointeur global
`0x897C6E24`. Sur le titre vivant, à l'écran FOOTBALL CLUB :

```text
objet 0xBF701E40
  +0x080 = 2      <- la garde exige 1
  +0x084 = 1      satisfait
  +0x48C = 1      POW_IS_ON, satisfait
```

**Un seul terme sur trois manque, et c'est l'état de connexion.**

### Ce que vaut « 2 »

Neuf sites comparent `+0x80` à 1, un seul à 2, et c'est celui-ci :

```text
0x89768034  lwz    r11, 0x80(r31)
0x89768038  cmpwi  r11, 2
0x8976803C  bne    -> sort
0x89768040  ld     r11, 0x3a90(r31)      un handle 64 bits
0x89768044  cmpldi r11, 0
0x89768048  mr     r11, r29              (r29 = 1)
0x8976804C  bne    0x89768054
0x89768050  mr     r11, r30              (r30 = 0)
0x89768054  clrlwi. r11, r11, 0x18
0x89768058  beq    -> sort               <- on sort ici
0x8976805C  mftb   ...                   l'échéance, jamais atteinte
```

L'état 2 est donc une **attente de reconnexion**, et la suite lit le compteur
de temps pour comparer à une échéance en `+0x3AA8`. Mais la branche teste
`+0x3A90` **avant** l'horloge, et sur la console ce champ est nul :

```text
+0x080  = 2                      stable sur trois relevés à dix secondes
+0x3A90 = 0x0000000000000000
+0x3AA8 = 46689531059            l'échéance, jamais consultée
```

Avec un champ nul, `r11` prend r30 = 0 et la branche sort immédiatement. Elle
n'atteint jamais l'horloge, donc jamais la tentative. C'est cohérent avec la
mesure du crochet XAM : zéro appel à `connect`, jamais.

Et la seule écriture 64 bits sur ce champ dans tout le module écrit **zéro**
(`0x89768088  std r30, 0x3A90(r31)`). Aucune écriture 32 bits sur `0x3A90` ni
`0x3A94`. Ce qui le remplit n'est donc pas visible sous ce déplacement : très
probablement une écriture par un pointeur intérieur, ou une copie de structure.
C'est le prochain fil à tirer.

### Ce que ça vaut

La chaîne est complète, des tuiles grisées jusqu'à un handle nul :

    +0x3A90 nul -> la branche « état 2 » sort avant l'horloge
                -> aucune tentative de connexion, jamais
                -> l'état reste 2
                -> la garde (état == 1) échoue
                -> Catalogue, Classements, Alertes et Infos amis restent gris

Il n'y a plus d'hypothèse réseau à tester, et il n'y en a jamais eu. Ce qui
reste est un champ à remplir, et l'identité de ce qui devrait le remplir.


## Ce qui remplit `+0x3A90`, et pourquoi il est vide

L'écriture existe, et elle échappait au balayage pour une raison bête : elle
passe par un pointeur intérieur avec un déplacement de **zéro**. Chercher
`std rX, 0x3A90(rY)` ne pouvait pas la trouver ; chercher l'immédiat 0x3A90
sous n'importe quel opcode l'a trouvée du premier coup.

```text
0x8974906C  addi r29, r31, 0x3a90      r29 = &objet.minuteur
0x89749080  bl   0x89721328            configure(&minuteur, durée, 0)
0x89749084  ld   r11, 0x3a90(r31)
0x89749088  cmpldi r11, 0
0x8974908C  bne  -> 0x897490a0         déjà armé : on ne réarme pas
0x89749098  mftb r11, 0x10c            le compteur de temps
0x8974909C  std  r11, 0(r29)           <- l'armement
```

`+0x3A90` est donc un **horodatage d'armement**, pas un handle. Le petit objet
embarqué s'étend au-delà : `+0x3AA8` est son échéance, soit `+0x3A90 + 0x18`.

### Le minuteur n'a pas « jamais été armé » : il a expiré

La branche d'état 2, lue en entier, dit autre chose que ce que j'avais compris
d'abord :

```text
0x89768038  cmpwi  r11, 2              état == 2 ?
0x89768040  ld     r11, 0x3a90(r31)    armé ?
0x89768058  beq    -> sortie           non : on sort
0x8976805C  mftb   r11, 0x10c          oui : maintenant
0x89768060  ld     r10, 0x3aa8(r31)    l'échéance
0x8976807C  beq    -> sortie           pas encore expirée : on sort
0x89768084  stb    r29, 0x3a80(r31)    expiré : on lève un drapeau
0x89768088  std    r30, 0x3a90(r31)    et on **désarme**
0x89768090  std    r30, 0x3a98(r31)
0x8976809C  bl     ...                 puis on émet un événement vers "fe"
```

Sur la console, les quatre champs racontent la fin de l'histoire :

```text
+0x0080 = 2                l'attente de reconnexion
+0x3A80 = 1               <- le drapeau « échéance atteinte »
+0x3A90 = 0               <- désarmé par la ligne ci-dessus
+0x3A98 = 0
+0x3AA8 = 46689531059      l'échéance, périmée
```

Le minuteur **a tiré**. Il a levé son drapeau, s'est désarmé, et a émis son
événement vers le front-end. Depuis, la branche d'état 2 sort à sa première
condition, définitivement : plus rien ne peut se produire de ce côté.

Le drapeau `+0x3A80` est lu par un accesseur, `0x89733F50` -- « une reconnexion
est-elle due ? ». Il vaut 1, ce qui est très probablement pourquoi l'écran
propose « CONNEXION AUX SERVEURS EAS FC ».

### Où ça laisse le problème

Le module n'est pas bloqué avant d'essayer : il a essayé une fois, à
l'expiration de son minuteur, et l'essai n'a produit aucun `connect`. Puis il
s'est mis dans un état d'où rien ne le sort tout seul.

Les deux choses qui réarmeraient sont dans la même grande fonction,
`0x89748A34..0x897494FC` : elle configure les minuteurs, lit toute la
configuration `POW_*`, arme l'horodatage en `0x8974909C`, et met l'état à 1 en
`0x897493A8`. Elle n'a **aucun appelant direct** dans le module -- elle est
atteinte par table virtuelle -- et son adresse n'apparaît dans aucune des trois
sections sorties. Trouver son entrée de vtable est le prochain fil.

L'autre question, jumelle : ce que fait le bouton. Il est proposé parce que
`+0x3A80` vaut 1 ; son action ne remet ni l'état ni l'horodatage, puisque les
deux sont inchangés après l'appui. Un crochet sur `0x89748A34` répondrait aux
deux d'un coup -- appelé ou non appelé.
