# La deuxième console — ABadAvatar + XeUnshackle

Une Xbox 360 Slim d'origine, sur le dashboard **2.0.17559.0**, mise en état
« hacké » par logiciel le temps qu'elle reste allumée. Le but n'est pas de
jouer dessus : c'est d'obtenir **XBDM sur le port 730**, parce que c'est la
seule chose par laquelle ce dépôt sait parler à une console.

## Ce qu'on a choisi, et pourquoi

« BadAvatar ou BadUpdate » n'est pas un choix : **ABadAvatar est un des
déclencheurs de BadUpdate**, l'exploit hyperviseur de grimdoomer. Ce qu'on
choisit, c'est par quelle porte on le déclenche, et ce qu'on exécute derrière.

**La porte : ABadAvatar** (shutterbug2000). Les deux autres déclencheurs
demandent un jeu — Tony Hawk's American Wasteland (déprécié en v1.3) ou Rock
Band Blitz, dont le trial n'est plus téléchargeable depuis la fermeture du
Marketplace 360. ABadAvatar se déclenche sur l'écran de sélection de profil du
dashboard lui-même : rien à acheter, et la console s'auto-exploite au boot.

**La charge utile : XeUnshackle** (Byrom90), pas FreeMyXe. XeUnshackle applique
le patchset HV+kernel complet — les mêmes que xeBuild pose dans une NAND
RGH/JTAG — **et charge `launch.xex` (Dashlaunch) depuis la mémoire**. C'est
Dashlaunch qui lit `launch.ini`, et c'est `launch.ini` qui charge `Xbdm.xex`.
FreeMyXe s'arrête avant : il fait tourner du non signé, pas Dashlaunch.

Le `launch.ini` livré par XeUnshackle est **déjà** notre configuration :

```
plugin1 = Usb:\Xbdm.xex
plugin2 =                  <- libre
plugin3 = Usb:\JRPC2.xex
plugin4 =                  <- le plugin du dépôt, cf. docs/PLUGIN.md
plugin5 =                  <- libre
liveblock = true
```

C'est la même forme que sur la console 1 (`docs/PLUGIN.md` : `Usb0:\launch.ini`,
`plugin1 = Usb:\xbdm.xex`). Rien à réécrire.

## La clé USB

Elle est déjà assemblée : `~/Downloads/console2-usb/`.

```
USB-upstream-vPB1.0/     <- ABadAvatar vPB1.0 de shutterbug2000 (l'auteur)
USB-fork-v1.3-beta/      <- fork bibarub, ABadAvatar porté sur BadUpdate v1.3
```

Prendre **`USB-upstream-vPB1.0`** en premier — c'est la release de l'auteur de
l'exploit. Si le déclenchement est trop capricieux, essayer le fork, qui porte
le même déclencheur sur les étages v1.3 (2ᵉ étage de 362 Ko au lieu de 2,1 Mo).

Contenu, identique dans les deux :

```
BadUpdatePayload/BadUpdateExploit-*.bin    les étages de l'exploit
BadUpdatePayload/default.xex               XeUnshackle
BadUpdatePayload/BadStorage.xex.dll        XeUnshackle
Content/E0002FF78DFBDE7B/FFFE07D1/...      le profil porteur de l'avatar piégé
Xbdm.xex  JRPC2.xex  launch.ini            chargés par Dashlaunch au retour
```

Clé **formatée FAT32**, contenu copié **à la racine**. Elle doit rester branchée :
Dashlaunch recopie un exécutable auxiliaire sur le support et en a besoin.

## La procédure

1. Vérifier le dashboard : Paramètres → Système → Informations sur la console →
   `2.0.17559.0`. Vérifier aussi que les **données de mise à jour Avatar** sont
   installées (sans HDD, il faut passer par la mise à jour système 17559
   hors ligne sur USB — **pas par LIVE**, au cas où Microsoft corrigerait).
2. Paramètres → Profil → Préférences de connexion → **couper la connexion
   automatique**. L'exploit a besoin que la console reste sur l'écran de
   sélection de profil.
3. **Débrancher l'Ethernet et couper le Wi-Fi.** Non négociable, voir la
   section « ban » plus bas.
4. Brancher la clé, allumer la console, la laisser sur l'écran de profils.
5. Attendre. On sait que ça travaille au curseur qui bouge tout seul sur
   l'écran de profils et aux deux LED du ring qui s'échangent. **Ne pas se
   connecter au profil piégé** (il a un PIN exprès), ne pas sortir de l'écran :
   les deux interrompent l'exploit.
6. Réussite = ring entièrement vert, puis XeUnshackle se lance. ~30 % de
   réussite, jusqu'à 20 minutes. Au-delà : éteindre, recommencer à l'étape 4.
7. Rebrancher l'Ethernet, puis sortir de XeUnshackle — les plugins ne se
   chargent qu'à la sortie.

Ensuite, du Mac :

```sh
tools/console_preflight.py <ip-console-2>
CONSOLE_MOD=softmod XBOX=<ip-console-2> tools/fut.sh --launch
```

`tools/fut.sh` connaît déjà le mode `softmod` : il n'envoie **jamais**
`magicboot`, parce qu'un redémarrage emporte l'hyperviseur patché et rend la
console au Mac pour de bon. Il demande le dashboard et attend.

## Ce que ça coûte, honnêtement

**Non persistant.** Chaque extinction, chaque gel du titre = la console est
perdue pour le Mac jusqu'à ce qu'un humain relance l'exploit, soit jusqu'à 20
minutes. `docs/MATCHMAKING.md` et l'expérience de la console 1 disent que ce
projet gèle des titres régulièrement. Sur la console 1 (RGH) ça coûte un
reboot ; ici ça coûtera un quart d'heure. Une Slim est glitchable (Trinity,
Corona) : si le rythme devient insupportable, le RGH matériel reste la sortie.

**Le ban LIVE, et c'est la vraie décision.** Une retail bannie ne se débannit
pas — contrairement à une RGH. Or `docs/MATCHMAKING.md` établit que la couche
XNet du pair-à-pair a besoin des adresses fournies par le vrai Xbox LIVE :
« LIVE réel pour la couche console, serveur privé pour la couche EA morte ».
Mettre une console softmodée sur LIVE n'est pas gratuit.

Le `launch.ini` livré a `liveblock = true`, qui bloque la résolution DNS de
LIVE. **On le garde pour commencer**, et on essaie d'abord le maillage sur le
LAN, où les deux consoles sont pour la première fois côte à côte — les six
appariements de `docs/MATCHMAKING.md` avaient une console en France et une aux
États-Unis. Si le maillage se forme sans LIVE, la question ne se pose plus. Il
sera temps de la poser si, et seulement si, il échoue pour la couche XNet.

Ne **pas** installer de « stealth server » (xbGuard et compagnie). Le README de
XeUnshackle est explicite : l'usurpation change l'adresse MAC **dans la NAND, de
façon persistante**. Et le paquet tout-en-un `Devs52/ABadAvatar-AIO` qui circule
embarque en plus une boutique de jeux piratés — ce n'est pas ce qu'on met sur
cette console.

## Le club : rien à faire, et une chose à ne pas faire

Le serveur est multi-locataire depuis le 14 août. `SessionStore.issue` frappe
un `X-UT-SID` aléatoire par persona (`server/fifa14_blaze_server.py`), et
`request_persona` route sur ce jeton — pas sur l'en-tête nucleus, qui est
l'identifiant en clair. La persona vient du `nuc` que le client poste lui-même
dans `/ut/auth` (`auth_request_identity`).

Conséquence : **utiliser un profil Xbox différent sur la console 2.** Deux
consoles avec le même profil présentent le même `nuc`, tombent sur la même
persona, et partagent le club — les 959 M et 706 cartes de la console 1 se
feraient écraser.

## Il reste à faire, côté console 2

- Un HDD, et FIFA 14 extrait dans `Hdd:\Games\FIFA 14` avec le TU3
  (`tools/console_preflight.py` vérifie les trois : titre, install, TU).
- XeXMenu 1.2 ou Aurora sur la clé, pour lancer le titre à la main — sur un
  softmod le Mac ne peut pas rebooter la console vers le dashboard.
