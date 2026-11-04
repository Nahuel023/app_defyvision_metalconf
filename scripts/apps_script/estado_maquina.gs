/**
 * Estado de maquinas DefyVision en Google Sheets.
 *
 * Recibe el heartbeat de src/metrics/status_reporter.py (POST JSON) y pinta la
 * casilla de estado de cada maquina:
 *   EN_USO  -> verde
 *   REPOSO  -> amarillo
 *   APAGADA -> rojo  (aviso de cierre de la app, o sin heartbeat hace STALE_MIN)
 *
 * Hoja "Estado": A=Maquina, B=Estado, C=Ultima senal, D=Detalle por scanner.
 * Cada maquina nueva agrega su propia fila automaticamente.
 * Instalacion: ver LEEME.md en esta misma carpeta.
 */

const SHEET_NAME = 'Estado';
const TOKEN = '';          // si no esta vacio, debe coincidir con status_report.token
const STALE_MIN = 3;       // minutos sin heartbeat para declarar APAGADA

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

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sheet = getSheet_();
    const row = findOrCreateRow_(sheet, String(data.machine || 'Maquina'));
    const detail = Object.keys(data.scanners || {})
      .map(function (k) { return k + ': ' + data.scanners[k]; })
      .join(' | ');
    sheet.getRange(row, 2, 1, 3).setValues([[status, new Date(), detail]]);
    sheet.getRange(row, 2).setBackground(COLORS[status]);
  } finally {
    lock.releaseLock();
  }
  return reply_({ ok: true });
}

/** Disparador por tiempo (cada 1 minuto): marca APAGADA si no hay heartbeat. */
function checkStale() {
  const sheet = getSheet_();
  const last = sheet.getLastRow();
  if (last < 2) return;
  const values = sheet.getRange(2, 1, last - 1, 3).getValues();
  const limit = Date.now() - STALE_MIN * 60 * 1000;
  values.forEach(function (r, i) {
    const lastSeen = r[2] instanceof Date ? r[2].getTime() : 0;
    if (r[0] && r[1] !== 'APAGADA' && lastSeen < limit) {
      const row = i + 2;
      sheet.getRange(row, 2).setValue('APAGADA').setBackground(COLORS.APAGADA);
      sheet.getRange(row, 4).setValue('sin senal desde ' + (lastSeen ? r[2] : '-'));
    }
  });
}

/** Ejecutar UNA vez a mano: crea la hoja y el disparador de checkStale. */
function setup() {
  getSheet_();
  ScriptApp.getProjectTriggers()
    .filter(function (t) { return t.getHandlerFunction() === 'checkStale'; })
    .forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('checkStale').timeBased().everyMinutes(1).create();
}

function getSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.getRange(1, 1, 1, 4)
      .setValues([['Maquina', 'Estado', 'Ultima senal', 'Detalle']])
      .setFontWeight('bold');
    sheet.getRange('C:C').setNumberFormat('dd/MM/yyyy HH:mm:ss');
  }
  return sheet;
}

function findOrCreateRow_(sheet, machine) {
  const last = sheet.getLastRow();
  if (last >= 2) {
    const names = sheet.getRange(2, 1, last - 1, 1).getValues();
    for (let i = 0; i < names.length; i++) {
      if (String(names[i][0]) === machine) return i + 2;
    }
  }
  sheet.getRange(last + 1, 1).setValue(machine);
  return last + 1;
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
