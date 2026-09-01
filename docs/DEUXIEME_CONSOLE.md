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

**LIVE n'est pas optionnel, et c'est ce qui impose un stealth server.**
`docs/EASFC_NOT_CONNECTED.md` l'a mesuré profil contre profil : un profil local
reçoit « Vous devez être connecté à Xbox Live et aux serveurs EA », un profil
Xbox LIVE passe. Ce n'est donc pas qu'une affaire de maillage pair-à-pair —
**FUT lui-même refuse de s'ouvrir sans LIVE**. Et `docs/MATCHMAKING.md` ajoute
que la couche XNet du pair-à-pair a besoin des adresses fournies par LIVE :
« LIVE réel pour la couche console, serveur privé pour la couche EA morte ».

La console 1 le fait depuis le début : `docs/PLUGIN.md` montre
`plugin2 = Usb:\Cipher\Cipher.xex`. La console 2 a besoin du même montage —
Cipher (ou xbGuard) en `plugin2`, et `liveblock = false` dans le `launch.ini`,
alors que XeUnshackle le livre à `true`.

Deux conséquences pratiques :

* **Les plugins sont chargés au moment où Dashlaunch démarre**, c'est-à-dire à
  la sortie de XeUnshackle. Ajouter Cipher à une session déjà en cours est
  impossible : il faut écrire le `launch.ini` et les fichiers sur la clé, puis
  refaire l'exploit. Autant les poser avant de rallumer.
* **Rien de tout ça n'est distribué avec le dépôt.** `docs/SAFETY.md` et
  `README.md` interdisent d'y committer les fichiers ou la configuration
  Cipher/xbGuard, les clés console et les KV. La règle porte sur la
  distribution, pas sur l'usage : le montage local en a besoin, le dépôt ne le
  publie pas.

Le risque reste réel et il faut le connaître : une retail bannie ne se débannit
pas, alors qu'une RGH se rattrape. C'est précisément ce que le stealth server
sert à éviter, et c'est pour ça qu'il n'est pas facultatif ici. Faire un dump
de la NAND avant, comme le recommande XeUnshackle, est une précaution gratuite.

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

## Ce que la console 2 a donné — 1er septembre 2026

Premier essai d'ABadAvatar réussi du premier coup, en une vingtaine de minutes
d'écran de profils. Ce qui a été mesuré ensuite, par XBDM, sans rien casser :

```
adresse            192.168.1.45          (le Mac est passé en .43)
bannière           201- connected
XBDM               2.0.21076.11
carte mère         Waternoose / Trinity  -- glitchable, le RGH reste ouvert
noyau              2.0.17559.0
disque             245 Go, dont 243 libres
```

**Le build est le bon.** C'était le vrai risque de la soirée : toutes les
adresses statiques de ce dépôt ne valent que pour un `default.xex` précis. Le
disque de la console 2, corrigé par le TU3, donne `0x534C8977` — identique à la
console 1 — et `powdllzf` se mappe au même `0x89700000`. Rien à relocaliser.

**Le TU3 se pousse par le réseau.** `sendfile` accepte `Hdd:\`, donc
`work/tu3/tu3-original.stfs` a été écrit directement dans
`Hdd:\Content\0000000000000000\454109C3\000B0000\tu00000003_00000000` : 157 052 928
octets, taille vérifiée au bout, 4,4 minutes à 0,6 Mo/s en Wi-Fi. Le dashboard
retail l'applique tout seul au lancement. Ne **jamais** pousser
`tu3-codex-patched.stfs` à la place : `docs/TU3_STATIC_PATCH.md` explique
pourquoi il est illisible.

**Le jeu tourne depuis le disque, sans installation.** Sur 360 installer un jeu
ne dispense pas du disque dans le lecteur — ça n'achète que la vitesse de
chargement. `Content\...\454109C3\00007000` est donc absent et ce n'est pas une
anomalie.

**La clé USB s'écrit à distance.** `Usb:\` et `Usb0:\` sont refusés par XBDM,
mais **`\Device\Mass0\` marche**, en lecture comme en écriture. C'est ce qui
permet de préparer la session suivante — plugins, `launch.ini` — sans jamais
débrancher la clé de la console. Cipher et son `launch.ini` y ont été posés
ainsi.

Une correction que ça a values à `tools/fut.sh` : `launch_title` reconnaissait
le titre en cours par `*FIFA*`, ce qui marche pour
`Hdd:\Games\FIFA 14\default.xex` et pas pour un lancement depuis le disque. Le
script sautait donc `await_dashboard` et armait le lanceur sur un titre déjà
lancé, à attendre un `modload` qui ne pouvait plus venir. Il teste maintenant
aussi `*default.xex*`.

## Le stealth server : Cipher ne couvre pas les softmods

Mesuré le 1er septembre 2026, les deux consoles allumées ensemble, même Cipher
(loader 1.12 / cœur 1.71), même réseau, même dashboard 17559 :

```
console 1  RGH        Live status changed: 0x1510F0
                      Notify: Connected to Xbox Live       -> marche
console 2  ABadAvatar Live status changed: 0x8015190E (x6)
                      alterné avec 0x1510F1, jamais 0x1510F0  -> échoue
```

Cipher parle pourtant à son propre serveur depuis la console 2 : il s'y est
enregistré et affiche son essai. Ce n'est donc ni le réseau ni le service. La
seule variable qui reste entre les deux consoles est **le type d'exploit**, et
c'est exactement ce que les deux fournisseurs annoncent : xbGuard se présente
comme couvrant « RGH/JTAG/XDK **and Bad Update/Bad Avatar consoles** » et
propose un *Standby Mode* décrit comme utile aux softmods ; Cipher n'annonce
que RGH / JTAG / XDK.

xbGuard est donc posé sur la clé de la console 2 en `plugin2`, Cipher débranché
mais conservé, avec un `launch.ini.cipher` laissé sur la clé pour revenir en
arrière sans le Mac. Le paquet (`xbguard.live/xbGuard.zip`, téléchargeable sans
compte) livre exactement la même forme de `launch.ini` que la console 1 :
`plugin1 = xbdm`, `plugin2 = stealth`, `plugin3 = JRPC2`, `pingpatch = false`,
`liveblock = false`. Le Lite Mode est gratuit et annonce les *challenge
responses*, ce qui est tout ce dont ce projet a besoin.

### Deux choses apprises au passage, dont une erreur à ne pas refaire

**Le préfixe du XUID ne dit pas le type du profil.** Le dossier de profil de la
console 2 est `Hdd:\Content\E00006ED8DE43D44` — préfixe `E0`, que la littérature
associe aux profils hors ligne. J'en ai conclu que `psyko mg` était un profil
local, donc le cas `louaY` de `docs/EASFC_NOT_CONNECTED.md`. **C'était faux** :
une capture d'écran montre le badge XBOX LIVE sur ce profil. Sur cette
question, l'écran tranche et le nom de dossier ne prouve rien —
`tools/xbdm_screenshot.py` coûte deux secondes.

**FIFA teste la connexion LIVE au démarrage du titre, et ne repose plus la
question.** Lancer le jeu puis se connecter ne rattrape rien : il faut le profil
connecté à LIVE **avant** d'appuyer. C'est ce qui a produit « Vous devez être
connecté à Xbox Live et aux serveurs EA » alors que tout le reste était en
place — redirecteur vérifié, EAS FC pointé, TU3 patché.
