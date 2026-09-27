// Factify — hand-crafted interactions
document.addEventListener('DOMContentLoaded', () => {
  // confidence bars animate
  document.querySelectorAll('.confidence-fill[data-width]').forEach(el=>{
    const w = parseInt(el.getAttribute('data-width'),10);
    if(!isNaN(w)) requestAnimationFrame(()=> el.style.width = w+'%');
  });

  // demo typing effect for hero
  const demo = document.getElementById('heroDemoText');
  if(demo){
    const phrases=["Government announces new education policy: 5 key changes explained","Fact-check: viral video of flood in Delhi is from 2019","RBI clarifies UPI transaction limits: what you need to know"];
    let i=0, idx=0, typing=true;
    const type=()=>{
      const cur=phrases[i];
      if(typing){
        idx++; demo.textContent=cur.slice(0,idx);
        if(idx>=cur.length){ typing=false; setTimeout(type,1400); return; }
      } else {
        idx--; demo.textContent=cur.slice(0,idx);
        if(idx<=0){ typing=true; i=(i+1)%phrases.length; }
      }
      setTimeout(type, typing?22:12);
    };
    type();
  }

  // factcheck mode persistence via URL hash
  const tabs=document.querySelectorAll('[data-bs-toggle="pill"]');
  tabs.forEach(t=> t.addEventListener('shown.bs.tab', e=>{
    const id=e.target.getAttribute('data-bs-target');
    if(id) history.replaceState(null,'',id);
  }));

  // image fallback
  document.querySelectorAll('img').forEach(img=>{
    img.addEventListener('error', ()=>{
      if(!img.dataset.fallback){
        img.dataset.fallback=1;
        img.src='/static/images/news_placeholder.jpg';
      }
    });
  });
});
