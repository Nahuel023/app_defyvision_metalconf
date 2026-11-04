/**
 * Estado en vivo de equipos DefyVision en la planilla Cartera_PostVenta_Defymotion.
 *
 * Recibe el heartbeat de src/metrics/status_reporter.py (POST JSON) y pinta la
 * columna "Semáforo" de la hoja "Equipos" en la fila del equipo:
 *   EN_USO  -> verde
 *   REPOSO  -> amarillo
 *   APAGADA -> rojo  (aviso de cierre de la app, o sin heartbeat hace STALE_MIN)
 *
 * La fila se busca por ID (columna "ID"), asi sigue funcionando si se ordena la
 * tabla. La ultima senal y el detalle por scanner quedan en la nota de la celda.
 * Instalacion: ver LEEME.md en esta misma carpeta.
 */

const SPREADSHEET_ID = '1eeb7uY6YnTY3Hatma0Oo5lmCflkaOAqIKqOhkW0gkq0';
const SHEET_NAME = 'Equipos';
const HEADER_ROW = 4;
const ID_HEADER = 'ID';
const STATUS_HEADER = 'Semáforo';

// status_report.machine (config/app.yaml) -> ID del equipo en la hoja.
const MACHINES = {
  'Metalconf': 'EQ-014',
};

const TOKEN = '';          // si no esta vacio, debe coincidir con status_report.token
const STALE_MIN = 3;       // minutos sin heartbeat para declarar APAGADA

const LABELS = {
  EN_USO:  'EN USO',
  REPOSO:  'REPOSO',
  APAGADA: 'APAGADA',
};
const COLORS = {
  EN_USO:  '#34a853',
  REPOSO:  '#fbbc04',
  APAGADA: '#ea4335',
};

function doPost(e) {
  let data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return reply_({ ok: false, error: 'json invalido' });
  }
  if (TOKEN && data.token !== TOKEN) {
    return reply_({ ok: false, error: 'token invalido' });
  }
  const status = String(data.status || '');
  if (!COLORS[status]) {
    return reply_({ ok: false, error: 'estado desconocido' });
  }
  const machine = String(data.machine || '');
  const equipoId = MACHINES[machine];
  if (!equipoId) {
    return reply_({ ok: false, error: 'maquina desconocida: ' + machine });
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const detail = Object.keys(data.scanners || {})
      .map(function (k) { return k + ': ' + data.scanners[k]; })
      .join('\n');
    if (!paint_(equipoId, status, detail)) {
      return reply_({ ok: false, error: 'no se encontro ' + equipoId });
    }
    PropertiesService.getScriptProperties()
      .setProperty('last_' + equipoId, String(Date.now()));
  } finally {
    lock.releaseLock();
  }
  return reply_({ ok: true });
}

/** Disparador por tiempo (cada 1 minuto): marca APAGADA si no hay heartbeat. */
function checkStale() {
  const props = PropertiesService.getScriptProperties();
  const limit = Date.now() - STALE_MIN * 60 * 1000;
  Object.keys(MACHINES).forEach(function (machine) {
    const equipoId = MACHINES[machine];
    const lastSeen = Number(props.getProperty('last_' + equipoId) || 0);
    if (!lastSeen || lastSeen >= limit) return;
    if (props.getProperty('stale_' + equipoId) === String(lastSeen)) return;
    paint_(equipoId, 'APAGADA',
      'Sin senal desde ' + Utilities.formatDate(
        new Date(lastSeen), Session.getScriptTimeZone(), 'dd/MM/yyyy HH:mm:ss'));
    props.setProperty('stale_' + equipoId, String(lastSeen));
  });
}

/** Ejecutar UNA vez a mano: valida la hoja y crea el disparador de checkStale. */
function setup() {
  Object.keys(MACHINES).forEach(function (m) {
    if (!findCell_(MACHINES[m])) throw new Error('No se encontro ' + MACHINES[m]);
  });
  ScriptApp.getProjectTriggers()
    .filter(function (t) { return t.getHandlerFunction() === 'checkStale'; })
    .forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('checkStale').timeBased().everyMinutes(1).create();
}

function paint_(equipoId, status, detail) {
  const cell = findCell_(equipoId);
  if (!cell) return false;
  const stamp = Utilities.formatDate(
    new Date(), Session.getScriptTimeZone(), 'dd/MM/yyyy HH:mm:ss');
  cell.setValue(LABELS[status])
    .setBackground(COLORS[status])
    .setFontColor('#ffffff')
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setNote('Estado en vivo (DefyVision)\nActualizado: ' + stamp +
             (detail ? '\n' + detail : ''));
  return true;
}

function findCell_(equipoId) {
  const sheet = SpreadsheetApp.openById(SPREADSHEET_ID).getSheetByName(SHEET_NAME);
  if (!sheet) return null;
  const lastCol = sheet.getLastColumn();
  const headers = sheet.getRange(HEADER_ROW, 1, 1, lastCol).getValues()[0]
    .map(function (h) { return String(h).trim(); });
  const idCol = headers.indexOf(ID_HEADER) + 1;
  const statusCol = headers.indexOf(STATUS_HEADER) + 1;
  if (!idCol || !statusCol) return null;
  const lastRow = sheet.getLastRow();
  if (lastRow <= HEADER_ROW) return null;
  const ids = sheet.getRange(HEADER_ROW + 1, idCol, lastRow - HEADER_ROW, 1).getValues();
  for (let i = 0; i < ids.length; i++) {
    if (String(ids[i][0]).trim() === equipoId) {
      return sheet.getRange(HEADER_ROW + 1 + i, statusCol);
    }
  }
  return null;
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
