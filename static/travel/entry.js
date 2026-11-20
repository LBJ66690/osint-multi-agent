const page = new URLSearchParams(location.search);
if (page.get('workspace') === 'research') {
  if (page.has('travel_report')) document.documentElement.classList.add('journal-mode');
  else location.replace('/static/workspace.html');
}
