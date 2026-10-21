using System;
using System.Web.Mvc;
using Microsoft.Win32;

namespace Legacy.Web.Controllers
{
    public class HomeController : Controller
    {
        public ActionResult Index()
        {
            var user = System.Web.HttpContext.Current.User;
            AppDomain.CurrentDomain.SetData("user", user);
            using (var key = Registry.LocalMachine.OpenSubKey("SOFTWARE"))
            {
            }
            return View();
        }
    }
}
