(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) return;

  const React = sdk.React;
  const { useState, useEffect, useCallback, useRef } = React;
  const h = React.createElement;

  // ── API helpers ──────────────────────────────────────────
  const API_BASE = "/api/plugins/hermes-plugin-workspace-tree-navigator";

  async function fetchJSON(path) {
    const res = await fetch(API_BASE + path);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  function downloadUrl(filePath) {
    return API_BASE + "/download?path=" + encodeURIComponent(filePath);
  }

  function previewUrl(filePath) {
    return API_BASE + "/preview?path=" + encodeURIComponent(filePath);
  }

  // ── Icons (inline SVG) ───────────────────────────────────
  const DownloadIcon = () => h('svg', {
    width: 14, height: 14, viewBox: '0 0 24 24',
    fill: 'none', stroke: 'currentColor', strokeWidth: 2,
    strokeLinecap: 'round', strokeLinejoin: 'round',
    style: { verticalAlign: 'middle' }
  },
    h('path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }),
    h('polyline', { points: '7 10 12 15 17 10' }),
    h('line', { x1: '12', y1: '15', x2: '12', y2: '3' })
  );

  const EyeIcon = () => h('svg', {
    width: 14, height: 14, viewBox: '0 0 24 24',
    fill: 'none', stroke: 'currentColor', strokeWidth: 2,
    strokeLinecap: 'round', strokeLinejoin: 'round',
    style: { verticalAlign: 'middle' }
  },
    h('path', { d: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z' }),
    h('circle', { cx: '12', cy: '12', r: '3' })
  );

  const FileIcon = ({ ext }) => {
    if (ext === '.pdf') return h('span', { style: { marginRight: 8, fontSize: 14 } }, '📕');
    if (ext === '.json') return h('span', { style: { marginRight: 8, fontSize: 14 } }, '📋');
    if (ext === '.md') return h('span', { style: { marginRight: 8, fontSize: 14 } }, '📝');
    if (ext === '.py' || ext === '.js' || ext === '.ts') return h('span', { style: { marginRight: 8, fontSize: 14 } }, '💻');
    if (['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.tiff', '.ico'].includes(ext)) return h('span', { style: { marginRight: 8, fontSize: 14 } }, '🖼️');
    if (['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.wma'].includes(ext)) return h('span', { style: { marginRight: 8, fontSize: 14 } }, '🎵');
    if (['.mp4', '.webm', '.mkv', '.avi', '.mov', '.wmv', '.flv'].includes(ext)) return h('span', { style: { marginRight: 8, fontSize: 14 } }, '🎥');
    return h('span', { style: { marginRight: 8, fontSize: 14 } }, '📄');
  };

  // ── useMediaQuery hook ───────────────────────────────────
  function useMediaQuery(query) {
    const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
    useEffect(() => {
      const mq = window.matchMedia(query);
      const handler = (e) => setMatches(e.matches);
      mq.addEventListener('change', handler);
      return () => mq.removeEventListener('change', handler);
    }, [query]);
    return matches;
  }

  // ── TreeNode component ───────────────────────────────────
  function TreeNode({ node, path, depth, selectedFile, onSelect, onDownload }) {
    const [expanded, setExpanded] = useState(depth === 0);
    const isDir = node.type === 'directory';
    const isSelected = selectedFile && selectedFile.path === node.path;
    const nodePath = path ? `${path}/${node.name}` : node.name;

    if (isDir) {
      const chevron = expanded ? '▼' : '▶';
      return h('div', { key: nodePath },
        h('div', {
          style: {
            display: 'flex',
            alignItems: 'center',
            cursor: 'pointer',
            padding: '5px 6px',
            borderRadius: 4,
            fontWeight: 600,
            fontSize: 13,
            color: isSelected ? '#a29bfe' : '#fff',
            backgroundColor: isSelected ? 'rgba(162,155,254,0.08)' : 'transparent',
            userSelect: 'none',
            transition: 'background 0.1s',
          },
          onMouseEnter: (e) => { if (!isSelected) e.currentTarget.style.background = 'rgba(255,255,255,0.03)'; },
          onMouseLeave: (e) => { if (!isSelected) e.currentTarget.style.background = 'transparent'; },
        },
          h('div', {
            onClick: () => {
              setExpanded(!expanded);
              onSelect(node);
            },
            style: { display: 'flex', alignItems: 'center', flex: 1, minWidth: 0, overflow: 'hidden' }
          },
            h('span', { style: { marginRight: 6, fontSize: 10, width: 12, display: 'inline-block', transition: 'transform 0.15s' } }, chevron),
            h('span', { style: { marginRight: 8, fontSize: 14 } }, '📁'),
            h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, node.name),
            node.error ? h('span', { style: { marginLeft: 8, fontSize: 11, color: '#e74c3c' } }, '⚠ ' + node.error) : null,
          ),
          !node.error && h('button', {
            onClick: (e) => { e.stopPropagation(); onDownload(node); },
            title: 'Download folder as ZIP: ' + node.name,
            style: {
              background: 'none',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 4,
              cursor: 'pointer',
              padding: '2px 6px',
              color: 'rgba(255,255,255,0.5)',
              display: 'flex',
              alignItems: 'center',
              flexShrink: 0,
              marginLeft: 4,
              transition: 'all 0.15s',
            },
            onMouseEnter: (e) => { e.currentTarget.style.color = '#a29bfe'; e.currentTarget.style.borderColor = 'rgba(162,155,254,0.4)'; },
            onMouseLeave: (e) => { e.currentTarget.style.color = 'rgba(255,255,255,0.5)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)'; },
          },
            h(DownloadIcon),
          )
        ),
        expanded && node.children && node.children.length > 0 && h('div', {
          style: { borderLeft: '1px solid rgba(255,255,255,0.06)', marginLeft: 10, paddingLeft: 4 }
        },
          node.children.map(child => h(TreeNode, {
            key: nodePath + '/' + child.name,
            node: child,
            path: nodePath,
            depth: depth + 1,
            selectedFile,
            onSelect,
            onDownload
          }))
        ),
        expanded && node.children && node.children.length === 0 && h('div', {
          style: { padding: '4px 0 4px 28px', fontSize: 12, color: 'rgba(255,255,255,0.3)', fontStyle: 'italic' }
        }, 'empty')
      );
    }

    // File node
    const ext = node.extension || '';
    return h('div', {
      key: nodePath,
      style: {
        display: 'flex',
        alignItems: 'center',
        padding: '4px 6px 4px 22px',
        borderRadius: 4,
        cursor: 'pointer',
        fontSize: 13,
        color: isSelected ? '#a29bfe' : 'rgba(255,255,255,0.75)',
        backgroundColor: isSelected ? 'rgba(162,155,254,0.08)' : 'transparent',
        transition: 'all 0.1s',
        userSelect: 'none',
        position: 'relative',
      },
      onMouseEnter: (e) => { if (!isSelected) e.currentTarget.style.background = 'rgba(255,255,255,0.02)'; },
      onMouseLeave: (e) => { if (!isSelected) e.currentTarget.style.background = 'transparent'; },
    },
      h('div', {
        onClick: () => onSelect(node),
        style: { display: 'flex', alignItems: 'center', flex: 1, minWidth: 0, overflow: 'hidden' }
      },
        h(FileIcon, { ext }),
        h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, node.name),
      ),
      h('button', {
        onClick: (e) => { e.stopPropagation(); onDownload(node); },
        title: 'Download ' + node.name,
        style: {
          background: 'none',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 4,
          cursor: 'pointer',
          padding: '2px 6px',
          color: 'rgba(255,255,255,0.5)',
          display: 'flex',
          alignItems: 'center',
          flexShrink: 0,
          marginLeft: 4,
          transition: 'all 0.15s',
        },
        onMouseEnter: (e) => { e.currentTarget.style.color = '#a29bfe'; e.currentTarget.style.borderColor = 'rgba(162,155,254,0.4)'; },
        onMouseLeave: (e) => { e.currentTarget.style.color = 'rgba(255,255,255,0.5)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)'; },
      },
        h(DownloadIcon),
      )
    );
  }

  // ── FolderPreview component ────────────────────────────────
  function FolderPreview({ folder, onDownload, onSelectChild }) {
    const childFiles = folder.children || [];
    const fileCount = childFiles.filter(c => c.type === 'file').length;
    const dirCount = childFiles.filter(c => c.type === 'directory').length;

    return h('div', { style: { height: '100%', display: 'flex', flexDirection: 'column' } },
      // Header bar
      h('div', {
        style: {
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 20px',
          borderBottom: '1px solid rgba(255,255,255,0.05)',
          flexShrink: 0,
        }
      },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 10 } },
          h('span', { style: { fontSize: 24 } }, '📁'),
          h('div', null,
            h('h2', { style: { margin: 0, fontSize: 17, color: '#fff', fontWeight: 600 } }, folder.name),
            h('span', { style: { fontSize: 11, color: 'rgba(255,255,255,0.4)' } },
              `${fileCount} files, ${dirCount} subdirectories`
            ),
          ),
        ),
        h('button', {
          onClick: () => onDownload(folder),
          style: {
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '8px 16px',
            borderRadius: 8,
            background: '#6c63ff',
            color: '#fff',
            textDecoration: 'none',
            fontSize: 13,
            fontWeight: 600,
            border: 'none',
            cursor: 'pointer',
            transition: 'background 0.15s',
            flexShrink: 0,
          },
          onMouseEnter: (e) => e.currentTarget.style.background = '#5a52d5',
          onMouseLeave: (e) => e.currentTarget.style.background = '#6c63ff',
        },
          h(DownloadIcon),
          'Download Folder (ZIP)',
        ),
      ),

      // Content area
      h('div', { style: { flex: 1, overflow: 'auto', padding: 24 } },
        h('h3', { style: { margin: '0 0 16px 0', fontSize: 14, color: 'rgba(255,255,255,0.6)', fontWeight: 500 } }, 'Contents'),
        h('div', {
          style: {
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            background: 'rgba(255,255,255,0.02)',
            border: '1px solid rgba(255,255,255,0.05)',
            borderRadius: 12,
            padding: 16,
          }
        },
          childFiles.length === 0 ? h('p', { style: { color: 'rgba(255,255,255,0.4)', fontStyle: 'italic', margin: 0, fontSize: 13 } }, 'This folder is empty.')
          : childFiles.map(child => h('div', {
              key: child.path,
              onClick: () => onSelectChild(child),
              style: {
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '10px 12px',
                borderRadius: 8,
                cursor: 'pointer',
                background: 'rgba(255,255,255,0.01)',
                transition: 'all 0.15s',
              },
              onMouseEnter: (e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.03)'; e.currentTarget.style.transform = 'translateX(2px)'; },
              onMouseLeave: (e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.01)'; e.currentTarget.style.transform = 'none'; },
            },
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 10 } },
                h('span', null, child.type === 'directory' ? '📁' : '📄'),
                h('span', { style: { fontSize: 13, color: '#fff', fontWeight: 500 } }, child.name),
                child.size_human && h('span', { style: { fontSize: 11, color: 'rgba(255,255,255,0.4)' } }, `(${child.size_human})`)
              ),
              h('span', { style: { color: '#6c63ff', fontSize: 12 } }, child.type === 'directory' ? 'Open folder →' : 'View file →')
            ))
        )
      )
    );
  }

  // ── FilePreview component ─────────────────────────────────
  function FilePreview({ file, onDownload }) {
    const [content, setContent] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    useEffect(() => {
      if (!file) return;
      if (file.is_pdf || file.is_image || file.is_audio || file.is_video) {
        setContent(null);
        setLoading(false);
        return;
      }
      if (!file.previewable) {
        setContent(null);
        setLoading(false);
        setError('This file type cannot be previewed as text.');
        return;
      }

      setLoading(true);
      setError(null);
      fetchJSON(previewUrl(file.path))
        .then(data => { setContent(data.content); setLoading(false); })
        .catch(err => { setError(err.message); setLoading(false); });
    }, [file]);

    if (!file) return null;

    const isPdf = file.is_pdf;

    return h('div', { style: { height: '100%', display: 'flex', flexDirection: 'column' } },
      // Header bar
      h('div', {
        style: {
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 20px',
          borderBottom: '1px solid rgba(255,255,255,0.05)',
          flexShrink: 0,
        }
      },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 10 } },
          h(FileIcon, { ext: file.extension || '' }),
          h('div', null,
            h('h2', { style: { margin: 0, fontSize: 17, color: '#fff', fontWeight: 600 } }, file.name),
            h('span', { style: { fontSize: 11, color: 'rgba(255,255,255,0.4)' } },
              file.size_human + (file.modified ? ' · ' + new Date(file.modified).toLocaleDateString() : '')
            ),
          ),
        ),
        h('a', {
          href: downloadUrl(file.path),
          download: file.name,
          style: {
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '8px 16px',
            borderRadius: 8,
            background: '#6c63ff',
            color: '#fff',
            textDecoration: 'none',
            fontSize: 13,
            fontWeight: 600,
            border: 'none',
            cursor: 'pointer',
            transition: 'background 0.15s',
            flexShrink: 0,
          },
          onMouseEnter: (e) => e.currentTarget.style.background = '#5a52d5',
          onMouseLeave: (e) => e.currentTarget.style.background = '#6c63ff',
        },
          h(DownloadIcon),
          'Download',
        ),
      ),

      // Content area
      h('div', { style: { flex: 1, overflow: 'auto', padding: 20 } },
        isPdf ? h('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 16 } },
          h('span', { style: { fontSize: 48 } }, '📕'),
          h('p', { style: { color: 'rgba(255,255,255,0.6)', fontSize: 14, textAlign: 'center' } },
            'PDF files are best viewed by downloading. Click the Download button above to open this file.'
          ),
          h('a', {
            href: downloadUrl(file.path),
            download: file.name,
            style: {
              padding: '10px 24px',
              borderRadius: 8,
              background: '#6c63ff',
              color: '#fff',
              textDecoration: 'none',
              fontSize: 14,
              fontWeight: 600,
            }
          }, 'Download ' + file.name),
        )
        : file.is_image ? h('div', { style: { display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', padding: 20 } },
          h('img', {
            src: downloadUrl(file.path),
            alt: file.name,
            style: {
              maxWidth: '100%',
              maxHeight: '100%',
              objectFit: 'contain',
              borderRadius: 8,
              boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
              border: '1px solid rgba(255,255,255,0.08)'
            }
          })
        )
        : file.is_audio ? h('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 16 } },
          h('span', { style: { fontSize: 64 } }, '🎵'),
          h('audio', {
            src: downloadUrl(file.path),
            controls: true,
            style: { width: '80%', maxWidth: 400 }
          })
        )
        : file.is_video ? h('div', { style: { display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', padding: 20 } },
          h('video', {
            src: downloadUrl(file.path),
            controls: true,
            style: {
              maxWidth: '100%',
              maxHeight: '100%',
              borderRadius: 8,
              boxShadow: '0 8px 24px rgba(0,0,0,0.5)'
            }
          })
        )
        : loading ? h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'rgba(255,255,255,0.4)' } },
          h('span', null, 'Loading...')
        )
        : error ? h('div', { style: { padding: 40, color: '#e74c3c', textAlign: 'center' } },
          h('p', null, error),
          h('a', {
            href: downloadUrl(file.path),
            download: file.name,
            style: { color: '#a29bfe', textDecoration: 'underline', cursor: 'pointer', marginTop: 12, display: 'inline-block' }
          }, 'Download instead')
        )
        : content !== null ? h('pre', {
          style: {
            background: 'rgba(255,255,255,0.02)',
            border: '1px solid rgba(255,255,255,0.05)',
            borderRadius: 12,
            padding: 20,
            fontSize: 13,
            lineHeight: 1.65,
            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
            color: '#c5c5c9',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            margin: 0,
            boxShadow: 'inset 0 4px 10px rgba(0,0,0,0.3)',
          }
        }, content)
        : null,
      )
    );
  }

  // ── Main WorkspaceNavigator ───────────────────────────────
  function WorkspaceNavigator() {
    const [workspaces, setWorkspaces] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [selectedFile, setSelectedFile] = useState(null);
    const [sidebarOpen, setSidebarOpen] = useState(true);
    const isMobile = useMediaQuery('(max-width: 768px)');
    const sidebarRef = useRef(null);

    // Load tree
    useEffect(() => {
      fetchJSON("/tree")
        .then(data => {
          setWorkspaces(data.workspaces || []);
          setLoading(false);
        })
        .catch(err => {
          setError(err.message);
          setLoading(false);
        });
    }, []);

    // Auto-close sidebar on mobile when file selected
    useEffect(() => {
      if (isMobile && selectedFile) {
        setSidebarOpen(false);
      }
    }, [selectedFile, isMobile]);

    const handleDownload = useCallback((file) => {
      // Programmatic download via hidden anchor
      const a = document.createElement('a');
      a.href = downloadUrl(file.path);
      a.download = file.type === 'directory' ? file.name + '.zip' : file.name;
      a.target = '_blank';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }, []);

    const handleSelect = useCallback((file) => {
      setSelectedFile(file);
    }, []);

    // Close sidebar on overlay click (mobile)
    const handleOverlayClick = useCallback(() => {
      if (isMobile) setSidebarOpen(false);
    }, [isMobile]);

    if (loading) {
      return h('div', {
        style: {
          display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center',
          fontFamily: '"Outfit","Inter",sans-serif', color: 'rgba(255,255,255,0.4)', fontSize: 14
        }
      }, 'Loading workspace tree...');
    }

    if (error) {
      return h('div', {
        style: {
          display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center',
          fontFamily: '"Outfit","Inter",sans-serif', color: '#e74c3c', fontSize: 14, flexDirection: 'column', gap: 8
        }
      },
        h('span', null, '⚠ Unable to load workspace tree'),
        h('span', { style: { fontSize: 12, color: 'rgba(255,255,255,0.3)' } }, error),
      );
    }

    const sidebarWidth = isMobile ? '100%' : 320;
    const showOverlay = isMobile && sidebarOpen;

    return h('div', {
      style: {
        display: 'flex',
        height: 'calc(100vh - 64px)',
        fontFamily: '"Outfit","Inter",sans-serif',
        color: '#e0e0e0',
        backgroundColor: '#0f0f13',
        position: 'relative',
        overflow: 'hidden',
      }
    },
      // Mobile overlay
      showOverlay && h('div', {
        onClick: handleOverlayClick,
        style: {
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.5)', zIndex: 9,
        }
      }),

      // Sidebar
      h('div', {
        ref: sidebarRef,
        style: {
          width: sidebarWidth,
          minWidth: isMobile ? '100%' : 280,
          maxWidth: isMobile ? '100%' : 400,
          borderRight: isMobile ? 'none' : '1px solid rgba(255,255,255,0.05)',
          overflowY: 'auto',
          backgroundColor: '#13131a',
          zIndex: isMobile ? 10 : 1,
          position: isMobile ? 'fixed' : 'relative',
          top: isMobile ? 0 : 'auto',
          left: isMobile ? (sidebarOpen ? 0 : '-100%') : 'auto',
          bottom: isMobile ? 0 : 'auto',
          transition: isMobile ? 'left 0.25s ease' : 'none',
          boxShadow: isMobile ? '2px 0 20px rgba(0,0,0,0.5)' : 'none',
        }
      },
        h('div', { style: { padding: 16 } },
          h('div', {
            style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }
          },
            h('h3', {
              style: {
                margin: 0, fontSize: 14, textTransform: 'uppercase',
                opacity: 0.6, letterSpacing: '0.05em'
              }
            }, 'Workspace Explorer'),
            isMobile && h('button', {
              onClick: () => setSidebarOpen(false),
              style: {
                background: 'none', border: 'none', color: 'rgba(255,255,255,0.5)',
                cursor: 'pointer', fontSize: 18, padding: '0 4px',
              }
            }, '✕'),
          ),
          workspaces.length === 0 ? h('p', {
            style: { fontSize: 13, color: 'rgba(255,255,255,0.3)', fontStyle: 'italic' }
          }, 'No workspaces configured.')
          : workspaces.map(ws => h(TreeNode, {
            key: ws.relative_path,
            node: ws,
            path: '',
            depth: 0,
            selectedFile,
            onSelect: handleSelect,
            onDownload: handleDownload,
          })),
        ),
      ),

      // Main content / preview
      h('div', {
        style: {
          flex: 1,
          overflowY: 'auto',
          backgroundColor: '#0f0f13',
          display: 'flex',
          flexDirection: 'column',
        }
      },
        // Mobile hamburger
        isMobile && !selectedFile && h('button', {
          onClick: () => setSidebarOpen(true),
          style: {
            position: 'fixed', bottom: 20, right: 20, zIndex: 8,
            width: 48, height: 48, borderRadius: '50%',
            background: '#6c63ff', border: 'none', color: '#fff',
            fontSize: 20, cursor: 'pointer',
            boxShadow: '0 4px 16px rgba(108,99,255,0.4)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }
        }, '📁'),

        // Mobile back button when file selected
        isMobile && selectedFile && h('button', {
          onClick: () => { setSidebarOpen(true); setSelectedFile(null); },
          style: {
            padding: '8px 16px', margin: '8px 12px',
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.06)',
            borderRadius: 6, color: 'rgba(255,255,255,0.6)',
            fontSize: 12, cursor: 'pointer',
            alignSelf: 'flex-start',
          }
        }, '← Back to files'),

        selectedFile ? (
          selectedFile.type === 'directory'
            ? h(FolderPreview, { folder: selectedFile, onDownload: handleDownload, onSelectChild: handleSelect })
            : h(FilePreview, { file: selectedFile, onDownload: handleDownload })
        )
        : h('div', {
          style: {
            display: 'flex', height: '100%', alignItems: 'center',
            justifyContent: 'center', opacity: 0.4, flexDirection: 'column',
            gap: 12, padding: 40, textAlign: 'center',
          }
        },
          h('span', { style: { fontSize: 48 } }, '📁'),
          h('p', { style: { fontSize: 14 } }, 'Select a file from the explorer'),
          h('p', { style: { fontSize: 12, opacity: 0.6 } },
            'Click any file to preview · Use ▼ download buttons for PDFs & files'
          ),
        ),
      )
    );
  }

  registry.register('hermes-plugin-workspace-tree-navigator', WorkspaceNavigator);
})();
