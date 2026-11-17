document.addEventListener('DOMContentLoaded',()=>{
 document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(x=>x.classList.remove('active'));
  btn.classList.add('active');document.getElementById('tab-'+btn.dataset.tab)?.classList.add('active');
 }));
 const daily=window.hydovaDaily;
 if(daily?.length && document.getElementById('rainChart')) new Chart(document.getElementById('rainChart'),{
  type:'line',data:{labels:daily.map(x=>x.date),datasets:[{label:'Curah hujan rata-rata (mm)',data:daily.map(x=>x.rain),borderColor:'#168aad',backgroundColor:'#20a4c722',fill:true,tension:.25,pointRadius:2}]},
  options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:true}},scales:{y:{beginAtZero:true}}}
 });
 const chart=(id,rows,label,key)=>{const el=document.getElementById(id);if(!el||!rows)return;new Chart(el,{type:'line',data:{labels:rows.map(x=>x['Periode ulang (tahun)']),datasets:[{label,data:rows.map(x=>x[key]),borderColor:'#168aad',backgroundColor:'#20a4c722',fill:true,tension:.25}]},options:{responsive:true,maintainAspectRatio:false,scales:{y:{beginAtZero:true}}}})};
 chart('frequencyChart',window.frequencyRows,'Hujan rencana (mm)','Hujan rencana (mm)');
 chart('floodChart',window.floodRows,'Debit (m³/det)','Debit (m³/det)');
});