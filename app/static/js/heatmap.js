(function () {
  const ctx = window.HEATMAP_CONTEXT || {};
  const state = {
    records: [], filtered: [], mode: 'density', map: null, densityCircles: [], markers: [],
    geocoder: null, hoverInfoWindow: null, geocodeRunning: false,
    autoGeocodedFranchises: new Set(), geocodeFailures: new Set(),
  };
  const $ = (id) => document.getElementById(id);

  function esc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function numeric(v) {
    if (v === null || v === undefined || String(v).trim() === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  function hasPoint(r) {
    const latitude = numeric(r.latitude);
    const longitude = numeric(r.longitude);
    return latitude !== null && longitude !== null &&
      latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180;
  }
  function fullAddress(r) { return r.fullAddress || [r.address, r.city, r.province, r.country || 'South Africa'].filter(Boolean).join(', '); }
  function recordTypeLabel(r) {
    return String(r.recordType || 'deceased').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
  }
  function recordName(r) {
    const deceased = [r.deceasedName, r.deceasedSurname].filter(Boolean).join(' ').trim();
    const nextOfKin = [r.nextOfKinName, r.nextOfKinSurname].filter(Boolean).join(' ').trim();
    return r.recordType === 'next_of_kin' ? (nextOfKin || deceased || 'Next of Kin') : (deceased || nextOfKin || recordTypeLabel(r));
  }
  function markerTitle(r) {
    return [
      recordName(r),
      `Category: ${recordTypeLabel(r)}`,
      r.mfFile ? `MF File: ${r.mfFile}` : '',
      r.contactNumber ? `Contact: ${r.contactNumber}` : '',
      fullAddress(r) ? `Address: ${fullAddress(r)}` : '',
    ].filter(Boolean).join('\n');
  }

  const venueTypes = new Set(['church', 'cemetery', 'crematorium']);

  function venueGroupKey(record) {
    const address = fullAddress(record).trim().toLowerCase();
    const coordinates = hasPoint(record)
      ? `${Number(record.latitude).toFixed(5)},${Number(record.longitude).toFixed(5)}` : '';
    const fallback = `${recordName(record)}|${record.city || ''}|${record.province || ''}`.toLowerCase();
    return `${record.recordType}|${address || coordinates || fallback}`;
  }

  function groupVenueRecords(records) {
    const groups = new Map();
    records.filter(record => venueTypes.has(record.recordType)).forEach(record => {
      const key = venueGroupKey(record);
      if (!groups.has(key)) {
        groups.set(key, {
          key,
          name: recordName(record),
          recordType: record.recordType,
          address: fullAddress(record),
          city: record.city || '',
          province: record.province || '',
          latitude: record.latitude,
          longitude: record.longitude,
          services: new Map(),
        });
      }
      const serviceKey = record.mfFile
        ? `${record.mfFile}|${record.dod || ''}` : `record:${record.id}`;
      if (!groups.get(key).services.has(serviceKey)) {
        groups.get(key).services.set(serviceKey, record);
      }
    });
    return [...groups.values()];
  }

  function venuePopup(group) {
    const services = [...group.services.values()];
    const serviceRows = services.map(record => {
      const deceased = [record.nextOfKinName, record.nextOfKinSurname].filter(Boolean).join(' ').trim() || 'Service record';
      return `<div style="padding:8px 0;border-top:1px solid #e5e0e8">
        <strong>${esc(deceased)}</strong>
        <div><b>MF File:</b> ${esc(record.mfFile || '-')}</div>
        <div><b>Date of death:</b> ${esc(record.dod || '-')}</div>
        ${record.contactNumber ? `<div><b>Contact:</b> ${esc(record.contactNumber)}</div>` : ''}
      </div>`;
    }).join('');
    return `<div class="heatmap-popup" style="min-width:280px;max-width:420px">
      <strong>${esc(group.name || recordTypeLabel({ recordType: group.recordType }))}</strong>
      <div><b>Category:</b> ${esc(recordTypeLabel({ recordType: group.recordType }))}</div>
      <div><b>Address:</b> ${esc(group.address || '-')}</div>
      <div><b>Total services:</b> ${services.length}</div>
      <div style="max-height:280px;overflow:auto;margin-top:8px">${serviceRows}</div>
    </div>`;
  }

  function filteredRecords() {
    const town = ($('heatTownFilter')?.value || '').trim().toLowerCase();
    const province = String($('heatProvinceFilter')?.value || '').trim().toLowerCase();
    const recordType = String($('heatRecordTypeFilter')?.value || '').trim().toLowerCase();
    state.filtered = state.records.filter(r => {
      const searchable = [r.city, fullAddress(r), recordName(r), r.mfFile, r.contactNumber]
        .map(value => String(value || '').toLowerCase()).join(' ');
      return (!town || searchable.includes(town)) &&
             (!province || String(r.province || '').trim().toLowerCase() === province) &&
             (!recordType || String(r.recordType || 'deceased').trim().toLowerCase() === recordType);
    });
    return state.filtered;
  }

  function updateStats() {
    const records = state.filtered || [];
    const mapped = records.filter(hasPoint).length;
    if ($('heatTotal')) $('heatTotal').textContent = records.length;
    if ($('heatMapped')) $('heatMapped').textContent = mapped;
    if ($('heatUnmapped')) $('heatUnmapped').textContent = records.length - mapped;
    if ($('heatModeLabel')) $('heatModeLabel').textContent = state.mode.charAt(0).toUpperCase() + state.mode.slice(1);
  }

  function popup(record) {
    const mapsLink = hasPoint(record) ? `<a href="https://www.google.com/maps?q=${record.latitude},${record.longitude}" target="_blank" rel="noopener">Open in Google Maps</a>` : '';
    const deceased = [record.deceasedName, record.deceasedSurname].filter(Boolean).join(' ').trim();
    const nextOfKin = [record.nextOfKinName, record.nextOfKinSurname].filter(Boolean).join(' ').trim();
    const isVenue = ['church', 'cemetery', 'crematorium'].includes(record.recordType);
    const pastor = record.recordType === 'church' && String(record.relationship || '').startsWith('Pastor:')
      ? String(record.relationship).slice(7).trim() : '';
    return `<div class="heatmap-popup">
      <strong>${esc(recordName(record))}</strong>
      <div><b>Category:</b> ${esc(recordTypeLabel(record))}</div>
      <div><b>MF File:</b> ${esc(record.mfFile || '-')}</div>
      ${isVenue && nextOfKin ? `<div><b>Service client:</b> ${esc(nextOfKin)}</div>` : ''}
      ${!isVenue && deceased ? `<div><b>Deceased:</b> ${esc(deceased)}</div>` : ''}
      ${record.dod ? `<div><b>Date of death:</b> ${esc(record.dod)}</div>` : ''}
      ${!isVenue && nextOfKin ? `<div><b>Next of kin:</b> ${esc(nextOfKin)}</div>` : ''}
      ${pastor ? `<div><b>Pastor:</b> ${esc(pastor)}</div>` : ''}
      ${record.relationship && !pastor ? `<div><b>Relationship:</b> ${esc(record.relationship)}</div>` : ''}
      <div><b>Franchise:</b> ${esc(record.franchiseName || '-')}</div>
      <div><b>Town:</b> ${esc(record.city || '-')}</div>
      <div><b>Province:</b> ${esc(record.province || '-')}</div>
      <div><b>Address:</b> ${esc(fullAddress(record) || '-')}</div>
      <div><b>Contact:</b> ${esc(record.contactNumber || '-')}</div>
      ${mapsLink}
    </div>`;
  }

  function clearMap() {
    state.markers.forEach(m => m.setMap(null));
    state.markers = [];
    state.densityCircles.forEach(circle => circle.setMap(null));
    state.densityCircles = [];
  }

  function densityRadiusMetres(latitude, pixelRadius, weight) {
    const zoom = state.map?.getZoom() ?? 5;
    const metresPerPixel = 156543.03392 * Math.cos(latitude * Math.PI / 180) / Math.pow(2, zoom);
    const weightFactor = Math.min(2.25, Math.max(0.7, Math.sqrt(Math.max(0.1, weight))));
    return Math.max(25, pixelRadius * metresPerPixel * weightFactor);
  }

  function updateDensityCircleRadii() {
    state.densityCircles.forEach(circle => {
      circle.setRadius(densityRadiusMetres(
        circle.__densityLatitude,
        circle.__densityPixelRadius,
        circle.__densityWeight
      ));
    });
  }

  function renderDensityCircles(mapped, pixelRadius) {
    if (!(state.mode === 'density' || state.mode === 'both')) return;
    mapped.forEach(record => {
      const latitude = Number(record.latitude);
      const longitude = Number(record.longitude);
      const weight = Math.max(0.1, Number(record.weight || 1));
      const circle = new google.maps.Circle({
        map: state.map,
        center: { lat: latitude, lng: longitude },
        radius: densityRadiusMetres(latitude, pixelRadius, weight),
        clickable: false,
        strokeColor: '#5f3b73',
        strokeOpacity: 0.08,
        strokeWeight: 1,
        fillColor: '#7f3f98',
        fillOpacity: Math.min(0.34, 0.12 + Math.log2(weight + 1) * 0.035),
        zIndex: 1,
      });
      circle.__densityLatitude = latitude;
      circle.__densityPixelRadius = pixelRadius;
      circle.__densityWeight = weight;
      state.densityCircles.push(circle);
    });
  }

  function renderMap(fit) {
    if (!state.map || !window.google) return;
    clearMap();
    const radius = Number($('heatRadius')?.value || 30);
    const mapped = (state.filtered || []).filter(hasPoint);
    const bounds = new google.maps.LatLngBounds();

    mapped.forEach(record => {
      const pos = { lat: Number(record.latitude), lng: Number(record.longitude) };
      bounds.extend(pos);
      if ((state.mode === 'pins' || state.mode === 'both' || state.mode === 'clusters') && !venueTypes.has(record.recordType)) {
        const marker = new google.maps.Marker({ position: pos, map: state.map, title: markerTitle(record) });
        marker.addListener('mouseover', () => {
          state.hoverInfoWindow.setContent(popup(record));
          state.hoverInfoWindow.open({ anchor: marker, map: state.map });
        });
        marker.addListener('mouseout', () => state.hoverInfoWindow.close());
        marker.addListener('click', () => {
          state.hoverInfoWindow.setContent(popup(record));
          state.hoverInfoWindow.open({ anchor: marker, map: state.map });
        });
        state.markers.push(marker);
      }
    });

    if (state.mode === 'pins' || state.mode === 'both' || state.mode === 'clusters') {
      groupVenueRecords(mapped).forEach(group => {
        if (!hasPoint(group)) return;
        const serviceCount = group.services.size;
        const marker = new google.maps.Marker({
          position: { lat: Number(group.latitude), lng: Number(group.longitude) },
          map: state.map,
          title: `${group.name}: ${serviceCount} service${serviceCount === 1 ? '' : 's'}`,
          label: { text: String(serviceCount), color: '#ffffff', fontWeight: '700', fontSize: '12px' },
          icon: {
            path: google.maps.SymbolPath.CIRCLE,
            scale: 18,
            fillColor: '#68457a',
            fillOpacity: 0.95,
            strokeColor: '#ffffff',
            strokeWeight: 2,
          },
        });
        marker.addListener('click', () => {
          state.hoverInfoWindow.setContent(venuePopup(group));
          state.hoverInfoWindow.open({ anchor: marker, map: state.map });
        });
        state.markers.push(marker);
      });
    }

    renderDensityCircles(mapped, radius);

    if (fit && mapped.length) {
      if (mapped.length === 1) state.map.setCenter(bounds.getCenter());
      state.map.fitBounds(bounds);
    }
  }

  function renderTable() {
    const body = $('heatRows');
    if (!body) return;
    const records = state.filtered || [];
    const colspan = ctx.canModify ? 8 : 7;
    if (!records.length) {
      body.innerHTML = `<tr><td colspan="${colspan}">No heat map records found.</td></tr>`;
      return;
    }
    body.innerHTML = records.slice(0, 500).map(record => `
      <tr class="${hasPoint(record) ? '' : 'warning-row'}">
        <td>${esc(record.mfFile)}</td>
        <td>${esc(record.franchiseName)}</td>
        <td>${esc(recordName(record))}</td>
        <td>${esc(record.city)}</td>
        <td>${esc(record.province)}</td>
        <td>${esc(fullAddress(record))}</td>
        <td>${esc(record.contactNumber)}</td>
        ${ctx.canModify ? `<td><button type="button" class="btn small danger-btn" data-delete="${record.id}">Delete</button></td>` : ''}
      </tr>`).join('');
  }

  function renderVenueGroups() {
    const section = $('heatVenueGroups');
    const body = $('heatVenueGroupRows');
    const selectedType = $('heatRecordTypeFilter')?.value || '';
    if (!section || !body || !venueTypes.has(selectedType)) {
      if (section) section.hidden = true;
      return;
    }

    const rows = groupVenueRecords(state.filtered || [])
      .sort((a, b) => b.services.size - a.services.size || a.name.localeCompare(b.name));
    $('heatVenueGroupsTitle').textContent = `${recordTypeLabel({ recordType: selectedType })} service totals`;
    body.innerHTML = rows.length ? rows.map(group => `
      <tr><td>${esc(group.name)}</td><td>${esc(group.city)}</td><td>${esc(group.province)}</td><td><strong>${group.services.size}</strong></td></tr>
    `).join('') : '<tr><td colspan="4">No venue service records found.</td></tr>';
    section.hidden = false;
  }

  function applyFilters(fit) {
    filteredRecords();
    updateStats();
    renderVenueGroups();
    renderTable();
    try {
      renderMap(fit);
    } catch (error) {
      // A bad legacy coordinate or Maps API issue must not hide otherwise
      // valid records, totals, filters and table rows.
      console.error('Heat Map rendering failed', error);
    }
  }

  async function loadData(fit) {
    const franchiseId = $('heatFranchiseFilter')?.value || '';
    const url = new URL(ctx.dataUrl, window.location.origin);
    if (franchiseId) url.searchParams.set('franchise_id', franchiseId);
    const status = $('heatDataStatus');
    if (status) {
      status.className = 'alert';
      status.textContent = 'Loading Heat Map records...';
    }
    try {
      const res = await fetch(url.toString(), {
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!res.ok) throw new Error(`Heat map data request failed (${res.status})`);
      const contentType = res.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) throw new Error('Heat map data request returned an invalid response');
      const data = await res.json();
      if (!data || !Array.isArray(data.records)) throw new Error('Heat map data response is incomplete');
      state.records = data.records;
      applyFilters(fit);
      if (status) {
        status.className = 'alert success';
        status.textContent = `${state.records.length} map location(s) loaded for the selected franchise. One spreadsheet service row can create separate Deceased, Next of Kin and venue locations.`;
      }
      startAutomaticGeocoding();
    } catch (error) {
      console.error(error);
      state.records = [];
      applyFilters(false);
      const body = $('heatRows');
      if (body) body.innerHTML = '<tr><td colspan="8">Heat map data could not be loaded. Refresh the page and try again.</td></tr>';
      if (status) {
        status.className = 'alert danger';
        status.textContent = `Heat Map records could not be loaded: ${error.message || 'unknown error'}. Please refresh the page or contact Admin.`;
      }
    }
  }

  function selectedFranchiseKey() {
    const select = $('heatFranchiseFilter');
    if (!select) return '';
    if (select.value) return select.value;
    const branchOptions = [...select.options].filter(option => option.value);
    return branchOptions.length === 1 ? branchOptions[0].value : '';
  }

  function startAutomaticGeocoding() {
    if (!ctx.canGeocode || !state.geocoder || state.geocodeRunning) return;
    const franchiseKey = selectedFranchiseKey();
    if (!franchiseKey || state.autoGeocodedFranchises.has(franchiseKey)) return;
    state.autoGeocodedFranchises.add(franchiseKey);
    setTimeout(() => geocodeMissing({ manual: false }), 300);
  }

  function geocodeAddress(address, attempt = 0) {
    return new Promise(resolve => {
      state.geocoder.geocode(
        { address, componentRestrictions: { country: 'ZA' } },
        (results, status) => {
          if (status === 'OK' && results && results[0]) {
            resolve({ result: results[0], status });
            return;
          }
          if (status === 'OVER_QUERY_LIMIT' && attempt < 3) {
            setTimeout(() => resolve(geocodeAddress(address, attempt + 1)), 1200 * (attempt + 1));
            return;
          }
          resolve({ result: null, status });
        }
      );
    });
  }

  async function geocodeMissing({ manual = false } = {}) {
    if (!ctx.canGeocode || !state.geocoder || state.geocodeRunning) return;
    if (manual) state.geocodeFailures.clear();

    const addressGroups = new Map();
    state.records.filter(record => !hasPoint(record) && fullAddress(record)).forEach(record => {
      const key = fullAddress(record).trim().toLowerCase();
      if (!key || state.geocodeFailures.has(key)) return;
      if (!addressGroups.has(key)) addressGroups.set(key, { address: fullAddress(record), records: [] });
      addressGroups.get(key).records.push(record);
    });
    const groups = [...addressGroups.values()];
    const statusBox = $('heatDataStatus');
    if (!groups.length) {
      if (manual && statusBox) {
        statusBox.className = 'alert info';
        statusBox.textContent = 'No address with missing coordinates is available to retry. Entries without an address must be completed in the spreadsheet.';
      }
      return;
    }

    state.geocodeRunning = true;
    let mappedAddresses = 0;
    let mappedRecords = 0;
    let failedAddresses = 0;
    for (let index = 0; index < groups.length; index += 1) {
      const group = groups[index];
      if (statusBox) {
        statusBox.className = 'alert info';
        statusBox.textContent = `Adding map coordinates automatically: ${index + 1} of ${groups.length} unique address(es)...`;
      }
      const { result, status } = await geocodeAddress(group.address);
      if (result) {
        const latitude = result.geometry.location.lat();
        const longitude = result.geometry.location.lng();
        try {
          const response = await fetch(ctx.coordinateUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({
              recordIds: group.records.map(record => record.id), latitude, longitude,
            }),
          });
          if (!response.ok) throw new Error(`coordinate save failed (${response.status})`);
          group.records.forEach(record => {
            record.latitude = latitude;
            record.longitude = longitude;
          });
          mappedAddresses += 1;
          mappedRecords += group.records.length;
        } catch (error) {
          console.warn('Could not save geocoded coordinates', error);
          failedAddresses += 1;
        }
      } else {
        console.warn(`Could not geocode address (${status})`, group.address);
        state.geocodeFailures.add(group.address.trim().toLowerCase());
        failedAddresses += 1;
      }
      await new Promise(resolve => setTimeout(resolve, 220));
    }
    state.geocodeRunning = false;
    applyFilters(true);
    const addressless = state.records.filter(record => !hasPoint(record) && !fullAddress(record)).length;
    if (statusBox) {
      statusBox.className = failedAddresses || addressless ? 'alert warning' : 'alert success';
      statusBox.textContent = `Automatic mapping completed: ${mappedRecords} location(s) at ${mappedAddresses} unique address(es) mapped. ${failedAddresses} address(es) could not be matched; ${addressless} location(s) have no address to map.`;
    }
  }

  async function deleteRecord(id) {
    if (!ctx.canModify || !confirm('Delete this heat map record?')) return;
    const res = await fetch(`/heat-map/record/${encodeURIComponent(id)}/delete`, { method: 'POST' });
    if (!res.ok) { alert('Could not delete record.'); return; }
    await loadData(false);
  }

  window.initMartinsHeatMap = function () {
    state.map = new google.maps.Map($('heatMap'), { center: { lat: -28.4793, lng: 24.6727 }, zoom: 5, mapTypeId: 'roadmap' });
    state.geocoder = new google.maps.Geocoder();
    state.hoverInfoWindow = new google.maps.InfoWindow();
    state.map.addListener('zoom_changed', updateDensityCircleRadii);
    loadData(true);
  };

  document.addEventListener('DOMContentLoaded', function () {
    $('heatTownFilter')?.addEventListener('input', () => applyFilters(false));
    ['heatProvinceFilter', 'heatRecordTypeFilter'].forEach(id => $(id)?.addEventListener('change', () => applyFilters(false)));
    $('heatRadius')?.addEventListener('input', () => renderMap(false));
    $('heatFranchiseFilter')?.addEventListener('change', () => loadData(true));
    $('heatFitBoundsBtn')?.addEventListener('click', () => renderMap(true));
    $('heatGeocodeBtn')?.addEventListener('click', () => geocodeMissing({ manual: true }));
    document.querySelectorAll('[data-heat-mode]').forEach(btn => btn.addEventListener('click', function () {
      state.mode = this.dataset.heatMode;
      document.querySelectorAll('[data-heat-mode]').forEach(b => b.classList.add('secondary'));
      this.classList.remove('secondary');
      applyFilters(false);
    }));
    $('heatRows')?.addEventListener('click', function (event) {
      const btn = event.target.closest('[data-delete]');
      if (btn) deleteRecord(btn.dataset.delete);
    });
    // Records and filters must work even if Google Maps is unavailable or its
    // external script is slow. The Maps callback will fit/reload once ready.
    loadData(false);
  });
})();
