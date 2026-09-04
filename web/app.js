/* The dashboard, in one file and no framework.
 *
 * Everything is a render function that takes the JSON the Python side already
 * shaped and returns markup. The server does the aggregating -- this only
 * draws -- which is why there is no state here beyond the last response and
 * which view is showing.
 */
'use strict';

const VIEWS = ['accueil', 'joueurs', 'activite', 'economie', 'serveur', 'joueur'];
const REFRESH_MS = 8000;

const state = {
  token: localStorage.getItem('fut14-token') || new URLSearchParams(location.search).get('k') || '',
  view: 'accueil',
  persona: 0,
  home: null,
  economy: null,
  server: null,
  feed: null,
  filter: '',
  verbose: false,
  timer: null,
};

/* ------------------------------------------------------------- helpers */

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/* Thin spaces between thousands, the way the console prints coins. */
function num(value) {
  const n = Number(value || 0);
  return n.toLocaleString('fr-FR').replace(/ | /g, ' ');
}

function shortNum(value) {
  const n = Number(value || 0);
  if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1).replace('.', ',') + ' Md';
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace('.', ',') + ' M';
  if (n >= 1e4) return Math.round(n / 1e3) + ' k';
  return num(n);
}

function ago(epoch) {
  if (!epoch) return 'jamais';
  const seconds = Math.max(0, Date.now() / 1000 - epoch);
  if (seconds < 60) return "à l'instant";
  if (seconds < 3600) return `il y a ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `il y a ${Math.floor(seconds / 3600)} h`;
  return `il y a ${Math.floor(seconds / 86400)} j`;
}

function duration(seconds) {
  if (!seconds && seconds !== 0) return '—';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days} j ${hours} h`;
  if (hours) return `${hours} h ${minutes} min`;
  return `${minutes} min`;
}

function clock(iso) {
  if (!iso) return '';
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return '';
  return new Date(at).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

/* Which FUT card a rarity draws as. The saves carry EA's own wording, so this
   matches on it rather than on the rareflag, which is not in every item. */
function rarityClass(rarity) {
  const label = String(rarity || '').toLowerCase();
  if (label.includes('year')) return 'toty';
  if (label.includes('season')) return 'tots';
  if (label.includes('week')) return 'totw';
  if (label.includes('legend') || label.includes('icon')) return 'legend';
  if (label.includes('silver')) return 'silver';
  if (label.includes('bronze')) return 'bronze';
  return '';
}

/* ---------------------------------------------------------------- data */

async function api(path) {
  const response = await fetch(path, { headers: { 'X-Admin-Token': state.token } });
  if (response.status === 401) {
    const error = new Error('unauthorised');
    error.unauthorised = true;
    throw error;
  }
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function setStatus(text, tone) {
  const node = $('status');
  node.textContent = text;
  node.style.color = tone === 'bad' ? 'var(--red)' : '';
}

/* --------------------------------------------------------------- tiles */

function tile(title, body, options = {}) {
  const classes = ['tile', options.hero ? 'hero' : '', options.link ? 'link' : '', options.cls || ''];
  const attrs = options.attrs || '';
  return `<div class="${classes.filter(Boolean).join(' ')}" ${attrs}>
    <h2 class="tile-title ${options.gold ? 'gold' : ''}">${esc(title)}</h2>
    <div class="tile-rule"></div>
    ${body}
  </div>`;
}

function figure(value, label, tone) {
  return `<div class="figure ${tone || ''}">${value}<small>${esc(label || '')}</small></div>`;
}

function rows(pairs) {
  return `<div class="rows">${pairs.map(([k, v]) => (
    `<div class="row"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`
  )).join('')}</div>`;
}

function eventRow(item) {
  const who = item.player ? `<span class="who">${esc(item.player)}</span>` : '<span></span>';
  const repeats = item.count > 1 ? ` <span class="times">×${item.count}</span>` : '';
  const detail = item.detail ? ` <span>— ${esc(item.detail)}</span>` : '';
  return `<div class="event cat-${esc(item.category)} lvl-${esc(item.level)}">
    <span class="when">${esc(clock(item.time))}</span>
    <span class="what"><b>${esc(item.title)}</b>${repeats}${detail}</span>
    ${who}
  </div>`;
}

function feedBlock(items, emptyNote) {
  if (!items || !items.length) return `<p class="empty-note">${esc(emptyNote || 'Rien pour le moment.')}</p>`;
  return `<div class="feed">${items.map(eventRow).join('')}</div>`;
}

/* The card database carries legal names -- "C. Ronaldo dos Santos Aveiro",
   "Radamel Falcao García Zarate" -- and a FUT card shows the short one. The
   second word is that name in almost every case here: Messi, Neuer, Ribéry,
   Iniesta, Falcao, Piqué, Ronaldo. Taking the last two words instead, which
   is what this did first, produced "SANTOS AVEIRO". */
const PARTICLES = new Set(['da', 'das', 'de', 'del', 'dos', 'do', 'van', 'von',
  'der', 'den', 'di', 'la', 'le', 'el', 'al', 'bin', 'ben', 'ter', 'st']);

function shortName(full) {
  const words = String(full || '').split(/\s+/).filter(Boolean);
  if (!words.length) return '—';
  if (words.length === 1) return words[0];
  // A particle is not a name. "Robin van Persie" is Van Persie on the card and
  // "... da Silva" is Da Silva -- taking the second word alone printed "DA".
  if (PARTICLES.has(words[1].toLowerCase()) && words[2]) {
    return `${words[1]} ${words[2]}`;
  }
  return words[1];
}

function futCard(card) {
  const name = shortName(card.name);
  return `<div class="card ${rarityClass(card.rarity)}">
    ${card.untradeable ? '<span class="badge">NT</span>' : ''}
    <div class="top"><span class="rt">${esc(card.rating || '?')}</span><span class="ps">${esc(card.position || '')}</span></div>
    <div>
      <div class="nm" title="${esc(card.name || '')}">${esc(name)}</div>
      <div class="cl">${esc(card.club || card.nation || '')}</div>
    </div>
  </div>`;
}

function bars(timeline) {
  if (!timeline || !timeline.length) return '<p class="empty-note">Pas encore d\'historique.</p>';
  const peak = Math.max(1, ...timeline.map((slot) => slot.signal || 0));
  const body = timeline.map((slot) => {
    const height = Math.round(((slot.signal || 0) / peak) * 100);
    const when = new Date(slot.hour * 1000).toLocaleString('fr-FR', { weekday: 'short', hour: '2-digit' });
    return `<div class="bar ${slot.signal ? '' : 'empty'}" style="height:${Math.max(2, height)}%"
      title="${esc(when)} — ${slot.signal || 0} actions"></div>`;
  }).join('');
  const first = new Date(timeline[0].hour * 1000);
  const last = new Date(timeline[timeline.length - 1].hour * 1000);
  const stamp = (d) => d.toLocaleString('fr-FR', { weekday: 'short', hour: '2-digit' });
  return `<div class="bars">${body}</div>
    <div class="bars-axis"><span>${esc(stamp(first))}</span><span>${esc(stamp(last))}</span></div>`;
}

function dist(items, labelKey, countKey) {
  if (!items || !items.length) return '<p class="empty-note">Aucune donnée.</p>';
  const peak = Math.max(1, ...items.map((item) => item[countKey]));
  return `<div class="dist">${items.map((item) => `
    <div class="dist-row">
      <span class="lab">${esc(item[labelKey])}</span>
      <span class="track"><span class="fill" style="width:${(item[countKey] / peak) * 100}%"></span></span>
      <span class="num">${item[countKey]}</span>
    </div>`).join('')}</div>`;
}

/* --------------------------------------------------------------- views */

function renderAccueil() {
  const data = state.home;
  if (!data) return;
  const o = data.overview;
  const players = data.players || [];
  const live = players.filter((p) => p.online);

  const roster = players.slice(0, 5).map((p) => `
    <div class="row">
      <span class="k">${esc(p.club || p.name)}</span>
      <span class="v">${p.online ? '<span class="pill on">en ligne</span>' : esc(ago(p.last_seen))}</span>
    </div>`).join('');

  $('view-accueil').innerHTML = `
    <div class="grid two" style="margin-bottom:10px">
      ${tile('Joueurs', `
        ${figure(o.players, live.length ? `${live.length} en ligne` : 'aucun en ligne', live.length ? 'green' : '')}
        <div class="rows" style="margin-top:.8rem">${roster}</div>
      `, { hero: true, gold: true, link: true, attrs: 'data-go="#/joueurs"' })}
      ${tile('Crédits en jeu', `
        ${figure(shortNum(o.coins), 'FUT coins', 'gold')}
        <p class="tile-note">Somme des soldes de tous les clubs. Chaque nouveau club démarre à 100 M.</p>
        <div class="rows" style="margin-top:.7rem">
          <div class="row"><span class="k">Cartes détenues</span><span class="v">${num(o.cards)}</span></div>
          <div class="row"><span class="k">Cartes tirées en packs</span><span class="v">${num(o.cards_pulled)}</span></div>
        </div>
      `, { link: true, attrs: 'data-go="#/economie"' })}
    </div>

    <div class="grid four" style="margin-bottom:10px">
      ${tile('Packs', figure(o.packs, 'ouverts'), { link: true, attrs: 'data-go="#/economie"' })}
      ${tile('Matchs', figure(o.matches, `${o.matches_today} aujourd'hui`, 'cyan'), {})}
      ${tile('Connexions', figure(o.logins, 'logins Blaze'), {})}
      ${tile('En service', figure(duration(o.uptime).split(' ')[0] + ' ' + (duration(o.uptime).split(' ')[1] || ''), 'sans redémarrage', 'green'), { link: true, attrs: 'data-go="#/serveur"' })}
    </div>

    <div class="grid wide">
      ${tile('Activité récente', feedBlock(data.feed, 'Le serveur tourne, personne ne joue.'),
        { link: true, attrs: 'data-go="#/activite"' })}
      <div class="grid stack">
        ${tile('Dernières 48 heures', bars(data.timeline), {})}
        ${tile('État du serveur', rows([
          ['Adresse annoncée', esc((data.server.ready || {}).advertise || '—')],
          ['Ports ouverts', (data.server.ports || []).map((p) => p.port).join(', ') || '—'],
          ['Clubs sur disque', data.server.clubs],
          ['Dernier évènement', esc(ago(o.last_event))],
          ['Demandes non gérées', `${o.gaps}`],
        ]), { link: true, attrs: 'data-go="#/serveur"' })}
      </div>
    </div>`;
}

function renderJoueurs() {
  const data = state.home;
  if (!data) return;
  const players = data.players || [];
  if (!players.length) {
    $('view-joueurs').innerHTML = tile('Joueurs', '<p class="empty-note">Aucun club sur ce serveur pour le moment.</p>', {});
    return;
  }
  const cards = players.map((p) => tile(p.club || p.name, `
    <div class="figure gold" style="font-size:2.1rem">${shortNum(p.coins)}<small>coins</small></div>
    ${rows([
      ['Manager', esc(p.name)],
      ['Note de l\'équipe', p.squad_rating || '—'],
      ['Cartes', num(p.cards)],
      ['Packs / matchs', `${p.packs} / ${p.matches}`],
      ['Vu', p.online ? '<span class="pill on">en ligne</span>' : esc(ago(p.last_seen))],
    ])}
  `, { link: true, hero: p.online, gold: p.online, attrs: `data-go="#/joueur/${p.persona_id}"` })).join('');

  $('view-joueurs').innerHTML = `
    <div class="grid three" style="margin-bottom:10px">${cards}</div>
    ${tile('Tous les clubs', `
      <table class="data">
        <thead><tr>
          <th>Club</th><th>Manager</th><th>Coins</th><th class="opt">Cartes</th>
          <th class="opt">Note</th><th class="opt">Packs</th><th class="opt">Matchs</th>
          <th>Adresse</th><th>Vu</th>
        </tr></thead>
        <tbody>${players.map((p) => `
          <tr class="link" data-go="#/joueur/${p.persona_id}">
            <td class="name">${esc(p.club || '—')}</td>
            <td>${esc(p.name)}</td>
            <td>${num(p.coins)}</td>
            <td class="opt">${num(p.cards)}</td>
            <td class="opt">${p.squad_rating || '—'}</td>
            <td class="opt">${p.packs}</td>
            <td class="opt">${p.matches}</td>
            <td>${esc(p.peer || '—')}</td>
            <td>${p.online ? '<span class="pill on">en ligne</span>' : esc(ago(p.last_seen))}</td>
          </tr>`).join('')}
        </tbody>
      </table>`, {})}`;
}

function renderJoueur(detail) {
  const s = detail.summary || {};
  const node = $('view-joueur');
  const xi = (detail.starters || []).map(futCard).join('');
  const bench = (detail.bench || []).map(futCard).join('');
  const best = (detail.best || []).map(futCard).join('');

  const seasons = (detail.seasons || []).map((season) => `
    <div class="row">
      <span class="k">Division ${esc(String(season.key).split(':')[1] || season.key)}</span>
      <span class="v">tour ${esc(season.round)} — ${esc(season.won || 0)}V ${esc(season.draw || 0)}N ${esc(season.lost || 0)}D</span>
    </div>`).join('') || '<p class="empty-note">Aucune saison entamée.</p>';

  const cups = (detail.tournaments || []).map((cup) => `
    <div class="row"><span class="k">Coupe ${esc(cup.key)}</span><span class="v">tour ${esc(cup.round)}</span></div>
  `).join('') || '<p class="empty-note">Aucune coupe en cours.</p>';

  node.innerHTML = `
    <button class="back" data-go="#/joueurs">‹ Tous les joueurs</button>
    <div class="grid two" style="margin-bottom:10px">
      ${tile(s.club || s.name || 'Club', `
        ${figure(num(s.coins), 'FUT coins', 'gold')}
        ${rows([
          ['Manager', esc(s.name || '')],
          ['Identifiant nucleus', esc(s.persona_id || '')],
          ['Première trace', esc(ago(s.first_seen))],
          ['Dernière trace', s.online ? '<span class="pill on">en ligne</span>' : esc(ago(s.last_seen))],
          ['Adresse', esc(s.peer || '—')],
        ])}
      `, { hero: true, gold: true })}
      ${tile('Inventaire', `
        ${figure(num(s.cards), 'cartes au club', 'cyan')}
        ${dist(detail.inventory || [], 'type', 'count')}
      `, {})}
    </div>

    <div class="grid two" style="margin-bottom:10px">
      ${tile(`Onze de départ — note ${s.squad_rating || '?'}`, xi ? `<div class="cards">${xi}</div>` :
        '<p class="empty-note">Pas encore d\'équipe enregistrée.</p>', { gold: true })}
      ${tile('Remplaçants et réserves', bench ? `<div class="cards">${bench}</div>` :
        '<p class="empty-note">Banc vide.</p>', {})}
    </div>

    <div class="grid three" style="margin-bottom:10px">
      ${tile('Saisons', seasons, {})}
      ${tile('Coupes', cups, {})}
      ${tile('Marché', rows([
        ['Cartes en vente', detail.listings],
        ['En attente de retrait', detail.pending],
        ['Objectifs validés', Object.keys(detail.tasks || {}).length],
      ]), {})}
    </div>

    <div class="grid wide">
      ${tile('Meilleures cartes du club', best ? `<div class="cards">${best}</div>` : '<p class="empty-note">—</p>', {})}
      ${tile('Ce que ce joueur a fait', feedBlock(detail.activity, 'Aucune action enregistrée.'), {})}
    </div>`;
}

function renderActivite() {
  const items = (state.feed || {}).feed || [];
  const filters = [
    ['', 'Tout'], ['session', 'Sessions'], ['club', 'Club'], ['economy', 'Économie'],
    ['match', 'Matchs'], ['market', 'Marché'], ['system', 'Système'], ['scan', 'Scans'],
  ];
  const chips = filters.map(([key, label]) => `
    <a href="#/activite" data-filter="${key}" class="${state.filter === key ? 'on' : ''}">${esc(label)}</a>
  `).join('');

  $('view-activite').innerHTML = `
    <nav class="nav" style="font-size:.8rem;margin-bottom:.8rem">${chips}
      <a href="#/activite" data-verbose="1" class="${state.verbose ? 'on' : ''}">${state.verbose ? 'Tout le bruit' : 'Masquer le bruit'}</a>
    </nav>
    ${tile(`Journal — ${items.length} lignes`, feedBlock(items), {})}`;
}

// Les intitulés des classements, et ce qu'une valeur veut dire.
//
// « Buts encaissés » se lit à l'envers : en prendre moins est mieux. Le
// serveur trie déjà dans le bon sens ; l'indiquer ici évite qu'un lecteur
// croie voir le pire gardien en tête.
const BOARDS = [
  { key: 'victoires',       title: 'Victoires',        unit: 'victoires' },
  { key: 'buts',            title: 'Buteurs',          unit: 'buts' },
  { key: 'matchs',          title: 'Matchs joués',     unit: 'matchs' },
  { key: 'tirs_cadres',     title: 'Tirs cadrés',      unit: 'cadrés' },
  { key: 'passes_reussies', title: 'Passes réussies',  unit: 'passes' },
  { key: 'tacles_reussis',  title: 'Tacles réussis',   unit: 'tacles' },
  { key: 'buts_encaisses',  title: 'Défenses',         unit: 'encaissés', asc: true },
];

function boardRows(rows, unit) {
  if (!rows || !rows.length) return '<p class="empty-note">Aucun match joué.</p>';
  return `<table class="board">${rows.map((row) => `
    <tr>
      <td class="rank">${row.rang}</td>
      <td class="who">${esc(row.name || String(row.persona_id))}</td>
      <td class="val">${num(row.valeur)}<span class="unit"> ${unit}</span></td>
    </tr>`).join('')}</table>`;
}

function matchLine(match) {
  const players = match.players || [];
  const first = players[0] || {};
  const scored = first.buts || 0;
  const conceded = first.buts_encaisses || 0;
  const when = match.when ? new Date(match.when).toLocaleString('fr-FR') : '';
  const name = esc(first.name || String(first.persona_id || ''));
  return `<li><b>${scored} – ${conceded}</b> ${name}
    <span class="muted">${esc(match.type || '')} ${when}</span></li>`;
}

function renderClassements() {
  const data = state.boards;
  if (!data) return;
  const boards = data.boards || {};
  $('view-classements').innerHTML = `
    <div class="grid four" style="margin-bottom:10px">
      ${tile('Matchs enregistrés', figure(num(data.played), 'depuis le premier rapport'), {})}
    </div>
    <div class="grid two" style="margin-bottom:10px">
      ${BOARDS.map((board) => tile(
        board.title + (board.asc ? ' — moins il y en a, mieux c\'est' : ''),
        boardRows(boards[board.key], board.unit),
        board.key === 'victoires' ? { gold: true } : {},
      )).join('')}
    </div>
    ${tile('Derniers matchs', (data.matches || []).length ?
      `<ul class="matches">${data.matches.map(matchLine).join('')}</ul>` :
      '<p class="empty-note">Aucun match joué pour l\'instant. Les classements se remplissent tout seuls : chaque partie terminée envoie son rapport.</p>', {})}`;
}

function renderEconomie() {
  const data = state.economy;
  const home = state.home;
  if (!data || !home) return;
  const o = home.overview;
  $('view-economie').innerHTML = `
    <div class="grid four" style="margin-bottom:10px">
      ${tile('Packs ouverts', figure(o.packs, 'depuis le démarrage'), {})}
      ${tile('Cartes tirées', figure(num(data.players_pulled), `joueurs, + ${num(data.objects_pulled)} objets`, 'cyan'), {})}
      ${tile('Ventes rapides', figure(num(data.quick_sold), 'cartes revendues'), {})}
      ${tile('Masse monétaire', figure(shortNum(o.coins), 'FUT coins', 'gold'), {})}
    </div>
    <div class="grid two" style="margin-bottom:10px">
      ${tile('Raretés tirées', dist(data.rarity, 'rarity', 'count'), {})}
      ${tile('Notes tirées', dist((data.ratings || []).map((band) => (
        { rarity: `${band.band}–${band.band + 4}`, count: band.count }
      )), 'rarity', 'count'), {})}
    </div>
    ${tile('Meilleurs tirages du serveur', (data.top || []).length ?
      `<div class="cards">${data.top.map(futCard).join('')}</div>` :
      '<p class="empty-note">Aucun pack ouvert pour l\'instant.</p>', { gold: true })}`;
}

function renderServeur() {
  const data = state.server;
  if (!data) return;
  const ready = data.server.ready || {};
  const gaps = data.gaps || { blaze: [], http: [] };

  const blaze = gaps.blaze.length ? `
    <table class="data"><thead><tr><th>Composant</th><th>Nom</th><th>Commande</th><th>Demandes</th></tr></thead>
    <tbody>${gaps.blaze.map((g) => `<tr>
      <td>${esc(g.component)}</td>
      <td class="name">${esc(g.name || '—')}</td>
      <td>${esc(g.command)}</td>
      <td>${g.count}</td></tr>`).join('')}</tbody>
    </table>` : '<p class="empty-note">Le titre n\'a rien demandé que le serveur ne sache faire.</p>';

  const http = gaps.http.length ? `
    <table class="data"><thead><tr><th>Méthode</th><th>Chemin</th><th>Demandes</th></tr></thead>
    <tbody>${gaps.http.map((g) => `<tr><td>${esc(g.method)}</td><td>${esc(g.path)}</td><td>${g.count}</td></tr>`).join('')}</tbody>
    </table>` : '<p class="empty-note">Aucune route FUT manquante.</p>';

  $('view-serveur').innerHTML = `
    <div class="grid two" style="margin-bottom:10px">
      ${tile('Processus', rows([
        ['Adresse annoncée', esc(ready.advertise || '—')],
        ['Port principal', esc(ready.core_port || '—')],
        ['Base identité', esc(ready.identity_base || '—')],
        ['Transport', esc(ready.redirector_transport || '—')],
        ['En service depuis', duration(data.overview.uptime)],
        ['Racine', esc(data.server.root)],
      ]), { hero: true })}
      ${tile('Ports', `
        ${rows((data.server.ports || []).map((p) => [`Port ${p.port}`, esc(p.transport)]))}
        <p class="tile-note" style="margin-top:.7rem">Composants Blaze annoncés : ${esc((ready.components || []).join(', '))}</p>
      `, {})}
    </div>

    <div class="grid two" style="margin-bottom:10px">
      ${tile('Ce que le jeu demande et n\'obtient pas — Blaze', `
        <p class="tile-note" style="margin-bottom:.8rem">Chaque ligne est un composant et une commande sans gestionnaire.
        C'est la liste que le titre tient lui-même de ce qu'il reste à écrire.</p>
        ${blaze}`, { gold: true })}
      ${tile('… et en HTTP', `
        <p class="tile-note" style="margin-bottom:.8rem">Routes FUT répondues 404. Les scans venus d'Internet sont écartés.</p>
        ${http}`, {})}
    </div>

    ${tile('Journal brut', feedBlock(data.feed), {})}`;
}

/* ------------------------------------------------------------- routing */

function currentRoute() {
  const hash = location.hash.replace(/^#\/?/, '');
  const [name, argument] = hash.split('/');
  if (name === 'joueur' && argument) return { view: 'joueur', persona: Number(argument) };
  return { view: VIEWS.includes(name) ? name : 'accueil', persona: 0 };
}

async function show() {
  const route = currentRoute();
  state.view = route.view;
  state.persona = route.persona;

  VIEWS.forEach((name) => $(`view-${name}`).classList.toggle('on', name === route.view));
  document.querySelectorAll('#nav a').forEach((link) => {
    const active = link.dataset.view === route.view
      || (route.view === 'joueur' && link.dataset.view === 'joueurs');
    link.classList.toggle('on', active);
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
  await load();
}

async function load() {
  try {
    if (!state.home || state.view === 'accueil' || state.view === 'joueurs') {
      state.home = await api('/api/overview?limit=40');
    }
    if (state.view === 'accueil') renderAccueil();
    else if (state.view === 'joueurs') renderJoueurs();
    else if (state.view === 'joueur') {
      renderJoueur(await api(`/api/players/${state.persona}`));
    } else if (state.view === 'activite') {
      const query = new URLSearchParams({ limit: '400' });
      if (state.filter) query.set('category', state.filter);
      if (state.verbose) query.set('verbose', '1');
      state.feed = await api(`/api/feed?${query}`);
      renderActivite();
    } else if (state.view === 'classements') {
      state.boards = await api('/api/leaderboards?limit=20');
      renderClassements();
    } else if (state.view === 'economie') {
      if (!state.home) state.home = await api('/api/overview?limit=40');
      state.economy = await api('/api/economy');
      renderEconomie();
    } else if (state.view === 'serveur') {
      state.server = await api('/api/server?limit=200');
      renderServeur();
    }
    if (state.home) capsule(state.home);
    setStatus(`à jour — ${new Date().toLocaleTimeString('fr-FR')}`);
  } catch (error) {
    if (error.unauthorised) return gate(true);
    setStatus(`hors ligne — ${error.message}`, 'bad');
  }
}

function capsule(data) {
  const o = data.overview;
  const online = (data.players || []).filter((p) => p.online).length;
  $('cap-players').textContent = o.players;
  $('cap-name').textContent = online ? `${online} EN LIGNE` : 'SERVEUR';
  $('cap-coins').textContent = shortNum(o.coins);
  $('cap-uptime').textContent = duration(o.uptime);
  const recent = (data.timeline || []).slice(-1)[0];
  const peak = Math.max(1, ...(data.timeline || []).map((slot) => slot.signal || 0));
  $('cap-meter').style.width = `${Math.min(100, ((recent && recent.signal) || 0) / peak * 100)}%`;
  const dot = $('cap-dot');
  const idle = o.last_event ? Date.now() / 1000 - o.last_event : 1e9;
  dot.className = 'capsule-dot ' + (online ? 'live' : idle < 3600 ? 'stale' : '');
}

/* ---------------------------------------------------------------- gate */

function gate(failed) {
  clearInterval(state.timer);
  $('app').hidden = true;
  $('gate').hidden = false;
  $('gate-error').hidden = !failed;
  $('gate-code').focus();
}

async function start() {
  $('gate').hidden = true;
  $('app').hidden = false;
  await show();
  clearInterval(state.timer);
  state.timer = setInterval(load, REFRESH_MS);
}

/* ---------------------------------------------------------------- boot */

document.addEventListener('click', (event) => {
  const chip = event.target.closest('[data-filter]');
  if (chip) {
    event.preventDefault();
    state.filter = chip.dataset.filter;
    load();
    return;
  }
  const toggle = event.target.closest('[data-verbose]');
  if (toggle) {
    event.preventDefault();
    state.verbose = !state.verbose;
    load();
    return;
  }
  const target = event.target.closest('[data-go]');
  if (target) {
    event.preventDefault();
    location.hash = target.dataset.go;
  }
});

document.addEventListener('keydown', (event) => {
  if ($('app').hidden) return;
  if (event.target.tagName === 'INPUT') return;
  const order = VIEWS.slice(0, 5);
  const at = order.indexOf(state.view === 'joueur' ? 'joueurs' : state.view);
  if (event.key === 'ArrowRight') location.hash = `#/${order[(at + 1) % order.length]}`;
  else if (event.key === 'ArrowLeft') location.hash = `#/${order[(at + order.length - 1) % order.length]}`;
  else if (event.key === 'Escape' && state.view === 'joueur') location.hash = '#/joueurs';
  else if (event.key === 'r' || event.key === 'x') load();
});

$('gate-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  state.token = $('gate-code').value.trim();
  try {
    await api('/api/overview?limit=1');
    localStorage.setItem('fut14-token', state.token);
    start();
  } catch (error) {
    gate(true);
  }
});

window.addEventListener('hashchange', show);

(async function boot() {
  // The URL can carry the code once; it is kept in localStorage afterwards so
  // it does not sit in the address bar of a shared screen.
  const fromUrl = new URLSearchParams(location.search).get('k');
  if (fromUrl) {
    localStorage.setItem('fut14-token', fromUrl);
    history.replaceState(null, '', location.pathname + location.hash);
  }
  const hello = await fetch('/api/hello').then((r) => r.json()).catch(() => ({ guarded: true }));
  if (!hello.guarded) { state.token = ''; return start(); }
  if (!state.token) return gate(false);
  try {
    await api('/api/overview?limit=1');
    start();
  } catch (error) {
    gate(Boolean(error.unauthorised));
  }
})();
