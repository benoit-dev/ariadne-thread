(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__
  var plugins = window.__HERMES_PLUGINS__
  if (!SDK || !plugins) return
  var h = SDK.React.createElement

  function AriadneThreadWeb() {
    return h(
      'div',
      { style: { padding: '24px', maxWidth: '640px' } },
      h('h2', null, "Ariadne's Thread"),
      h('p', null, "The flow view lives in Hermes Desktop: open the Ariadne's Thread page from the Desktop sidebar."),
      h('p', null, 'The web dashboard only hosts its API at /api/plugins/ariadne-thread/.')
    )
  }

  plugins.register('ariadne-thread', AriadneThreadWeb)
})()
