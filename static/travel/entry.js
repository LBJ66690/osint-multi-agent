const page = new URL(location.href);
const researchShell = document.documentElement.dataset.workspace === 'research';
if (page.searchParams.has('travel_report')) {
  if (researchShell) {
    page.pathname = '/';
    page.searchParams.delete('workspace');
    location.replace(page.href);
  } else {
    document.documentElement.classList.add('journal-mode');
  }
} else if (!researchShell && page.searchParams.get('workspace') === 'research') {
  location.replace('/static/workspace.html');
}
