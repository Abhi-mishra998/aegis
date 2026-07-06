import{r as c,f as e}from"./vendor-react-NHDOn6vd.js";import{M as v}from"./index-CWONLu3I.js";import{A as w}from"./vendor-icons-5QlxSFhX.js";function k({isOpen:o,title:u,description:a,confirmLabel:f="Confirm",cancelLabel:b="Cancel",variant:x="default",onConfirm:i,onClose:t,onError:r,icon:g}){const l=c.useRef(null),[s,n]=c.useState(!1),m=async()=>{if(!s)try{n(!0),await(i==null?void 0:i()),t==null||t()}catch(p){r==null||r(p)}finally{n(!1)}},d=x==="danger",h=d?"bg-red-500 hover:bg-red-400 text-white focus-visible:ring-red-300":"bg-white hover:bg-neutral-200 text-black focus-visible:ring-white/60";return e.jsx(v,{isOpen:o,onClose:s?()=>{}:t,title:u,size:"sm",initialFocusRef:l,footer:e.jsxs(e.Fragment,{children:[e.jsx("button",{ref:l,type:"button",onClick:t,disabled:s,className:`
              w-full sm:w-auto px-4 py-2 rounded-lg text-xs font-semibold
              text-neutral-200 bg-white/[0.04] hover:bg-white/[0.08]
              border border-[var(--border-default)]
              focus-visible:ring-2 focus-visible:ring-white/30
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors
            `,children:b}),e.jsxs("button",{type:"button",onClick:m,disabled:s,className:`
              w-full sm:w-auto px-4 py-2 rounded-lg text-xs font-semibold
              focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-surface-elevated)]
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors
              inline-flex items-center justify-center gap-2
              ${h}
            `,children:[s&&e.jsx("span",{className:"inline-block w-3 h-3 rounded-full border-2 border-current border-r-transparent animate-spin","aria-hidden":"true"}),f]})]}),children:e.jsxs("div",{className:"flex items-start gap-3",children:[(g??(d&&e.jsx(w,{className:"text-red-400 shrink-0 mt-0.5",size:18,"aria-hidden":"true"})))||null,a&&e.jsx("p",{className:"text-xs text-neutral-300 leading-relaxed",children:a})]})})}export{k as C};
