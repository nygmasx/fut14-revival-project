# Ce qu'Impulsum14 nous apporte, et ce qu'il faut refaire

`github.com/MarvelcoCode/Impulsum14` est un serveur FIFA 14 **PC** en C# / .NET 8
(10 448 lignes de serveur, plus un SDK Blaze généré). Il fait tourner FUT en
ligne sur PC, EAS FC compris. Ce document dit ce qui se transpose sur Xbox 360,
ce qui ne se transpose pas, et où nous étions déjà devant.

## La mesure d'abord

Avant de copier quoi que ce soit, il fallait savoir ce que notre console demande
et ce qu'elle reçoit. Le journal du VPS le dit :

    146  OSDK_TICKER              146  OSDK_ABUSE_REPORTING
    146  OSDK_ARENA               146  OSDK_ROSTER
    138  OSDK_CORE                138  OSDK_CLIENT
    138  OSDK_NUCLEUS             138  OSDK_WEBOFFER
    138  OSDK_XMS_ABUSE_REPORTING 138  OSDK_TOLLBOOTH
    138  OSDK_SOCIAL_NETWORKS      92  IdentityParams
     35  OSDK_CUSTOM_DATA           1  FIFA_H2H_SEASONALPLAY

**Quatorze sections demandées, quatre servies.** Les dix autres repartaient avec
une carte vide. Impulsum14, lui, renvoie *le même dictionnaire complet quelle que
soit la section demandée* -- et son EAS FC fonctionne.

Une section vide n'est pas une réponse neutre. Un module qui lit son URL et sa
période de reprise dans une section et ne reçoit rien n'a ni l'une ni l'autre, et
retombe sur des hôtes morts depuis 2016.

## Ce qui rejoint directement notre blocage EAS FC

Nous avions établi, en désassemblant `powdllzf`, que le module arme un horodatage
de reconnexion en `0x8974909C`, que ce minuteur **a expiré**, s'est désarmé
lui-même, et que rien ne l'a jamais réarmé. Il manquait la cause.

Impulsum14 sert une clé que nous ne servions pas :

    OSDK_EASW_CONNECT_RETRY_PERIOD = 30
    EASW/ENABLED                   = 1

Un module sans période de reprise configurée n'a rien avec quoi se réarmer. Ce
n'est pas une preuve -- c'est l'hypothèse la mieux étayée que nous ayons eue, et
elle est mesurable sur la console.

## Ce qui est fait

`Fifa14Protocol.shared_config()` sert désormais un bloc commun à **toutes** les
sections, fusionné avec les valeurs propres à chaque section (les nôtres gagnent,
elles viennent de l'image Xbox et non d'un serveur PC). Il contient le bloc EASW,
les onze commutateurs `POW/*`, la redirection RS4, les services qu'on éteint
explicitement plutôt que de les laisser à leur défaut, et les temporisations.

Le bloc est volontairement **plus étroit** que le dictionnaire d'Impulsum14 :

- tout ce qui pilote l'authentification (`AUTH_TYPE`, `USE_TOKEN_AUTH`,
  `NUCLEUS_*`, `ORIGIN_LOGIN_ENABLED`, `nucleusHost`) est PC et reste dehors :
  cette console passe par Xbox Live et ce chemin marche déjà ;
- tout ce qui pointe vers un service que nous ne servons pas (`CMS_*`, les
  arbres DIME et downloader) reste dehors : une URL qui répond 404 est pire
  qu'un commutateur laissé à son défaut.

Neuf tests tiennent la forme, dont « aucune clé servie deux fois dans une même
carte », « aucune URL partagée ne nomme un hôte qui n'est pas le nôtre » et les
deux qui viennent d'une erreur payée sur la console.

### L'erreur, et ce qu'elle a coûté

La première version servait le bloc à **toutes** les sections, comme le fait
Impulsum14. Le 27 août au soir, la console ne s'est plus connectée du tout :
elle s'authentifiait, prenait la redirection, fermait la connexion une seconde
plus tard, et recommençait toutes les soixante-dix secondes derrière « les
serveurs EA ne sont pas disponibles ». Quatre cycles identiques.

Le diagnostic a demandé de retirer les deux changements de la soirée d'un coup
— le bloc de configuration et un crochet posé dans XAM — puis de relancer : la
session a tenu, et le crochet n'avait jamais empêché une connexion TCP
d'aboutir. Le bloc était nommé sans ambiguïté.

La faute n'était pas dans le contenu du bloc mais dans sa portée. `OSDK_CORE`,
`OSDK_CLIENT`, `OSDK_ROSTER` et `IdentityParams` ont été retrouvées dans
l'image de cette console et vérifiées sur elle ; `OSDK_CORE` et `OSDK_CLIENT`
sont ce que lit CardsDLL. Cinquante clés tirées d'un serveur PC — dont
`ALLOW_OFFLINE`, `SKIP_LEGAL_DOC` et `OSDK_ONLINE_ENABLED` — n'ont pas à passer
devant. Le bloc ne va donc plus que là où il n'y avait rien : les dix sections
qui revenaient vides.

## Ce qu'ils ont et que nous n'avons pas

**Le SDK Blaze généré.** `SDK/Blaze3SDK` contient 919 classes TDF et 24
définitions de composants en JSON (`Components/*.json`), chacune avec les
identifiants de commande, les codes d'erreur nommés, et pour chaque membre son
tag, son type et son index. C'est la table de référence que nous n'avons jamais
eue -- `GetStatsByGroupRequest` en fait partie, et c'est une de nos questions
ouvertes. Elle est directement traduisible en tables Python.

**Les formes de données FUT** : barème des divisions (Seasons.cs), poids des
pochettes (PackWeights.cs), tournois, TOTW, staff, consommables.

## Ce qu'ils n'ont pas et que nous avons

Le comptage brut est en notre faveur sur les deux axes qui comptent :

| | Impulsum14 | nous |
|---|---|---|
| routes `/ut/...` | ~40 | **61** |
| composants Blaze enregistrés | 12 | **24** |

Nous servons en plus : `phishing` (les quatre routes), `store` et
`purchasegroup`, `eventfeed`, `managerquest`, `leaderboards/options`,
`season/list` et `season/user/history`, `trade/status`, `user/credits`,
`club/stats/newcards` et `club/stats/staff`.

Surtout : **ils n'enregistrent pas GameManager** (composant 4). Le maillage P2P,
notre blocage de plus longue date, n'est pas résolu chez eux -- il n'est pas
abordé.

Et le `RRST` / « Unsupported TDF type 201 for @PCN » qui traînait dans nos
questions ouvertes est déjà corrigé chez nous (`tools/blaze_tdf.py`) : c'était un
décodeur désynchronisé par une carte de structures vides, pas une grammaire
manquante.

## Ce que le README annonce et ce que le dépôt contient

À vérifier avant de s'appuyer dessus : le README liste **les divisions en TODO**
et, sous « Known Bugs », « Divisions are broken and not implemented completely »,
avec les tournois qui plantent en cours de route et le marché des transferts qui
plante depuis le comparateur de prix. `Seasons.cs` contient bien un barème des
dix divisions et les formes JSON associées, donc il y a de la matière -- mais pas
un chantier terminé.

## Ce qui ne se transpose pas

- **L'authentification.** Origin / Nucleus, `AuthenticationSource = "303107"`,
  `PersonaNamespace = "cem_ea_id"`, `Platform = "pc"`. Nous sommes en `300294` et
  Xbox Live.
- **Le lanceur.** Ils détournent les hôtes sous Windows ; nous rustinons le
  titre.
- **Les suffixes de plateforme.** Ils écrivent `FUT/MODULE_BASEURL_CARDS` ; le
  build 360 lit `FUT/MODULE_BASEURL_XBox360`. Leur arbre d'objets est sous
  `fut/items/pc/`.
- **Le langage.** C# / .NET 8 contre Python. Porter, ici, c'est traduire.

## Le prochain pas, et sa précaution

Déployer et regarder la console. Le déploiement redémarre le serveur Blaze, ce
qui **éjecte une session FUT en cours** -- donc hors session seulement. Ensuite,
reposer le crochet passif sur `NetDll_connect` et appuyer sur « CONNEXION AUX
SERVEURS EAS FC » : si un `connect` apparaît là où il n'y en avait aucun, la
période de reprise était bien la cause.
